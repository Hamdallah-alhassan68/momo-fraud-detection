import json
import joblib
import numpy as np
import pandas as pd

from config import (
    ISOLATION_FOREST_PATH,
    SCALER_PATH,
    MEDIANS_PATH,
    CONFIG_PATH
)

from services.feature_engineering import (
    build_features,
    ISO_FEATURES
)


class FraudDetector:

    def __init__(self):

        print("Loading V3 fraud detection model...")

        # --------------------------------------------------
        # Load trained artifacts
        # --------------------------------------------------

        self.isolation_forest = joblib.load(
            ISOLATION_FOREST_PATH
        )

        self.scaler = joblib.load(
            SCALER_PATH
        )

        self.train_medians = joblib.load(
            MEDIANS_PATH
        )

        with open(CONFIG_PATH, "r") as file:
            self.config = json.load(file)

        # --------------------------------------------------
        # V3 configuration
        # --------------------------------------------------

        self.if_weight = float(
            self.config["if_weight"]
        )

        self.rule_weight = float(
            self.config["rule_weight"]
        )

        self.risk_threshold = float(
            self.config["risk_threshold"]
        )

        self.low_threshold = float(
            self.config["low_threshold"]
        )

        self.medium_threshold = float(
            self.config["medium_threshold"]
        )

        self.amount_threshold = float(
            self.config["amount_threshold"]
        )

        self.velocity_threshold = float(
            self.config["velocity_threshold"]
        )

        self.velocity_window = int(
            self.config["velocity_window"]
        )

        # --------------------------------------------------
        # EXACT V3 Isolation Forest normalization
        # --------------------------------------------------

        self.iso_min = float(
            self.config["iso_min"]
        )

        self.iso_max = float(
            self.config["iso_max"]
        )

        # --------------------------------------------------
        # Verify feature consistency
        # --------------------------------------------------

        scaler_features = list(
            self.scaler.feature_names_in_
        )

        if scaler_features != ISO_FEATURES:

            raise ValueError(
                "Feature mismatch between V3 scaler "
                "and API feature engineering."
            )

        if len(self.train_medians) != len(
            ISO_FEATURES
        ):

            raise ValueError(
                "Training medians do not contain "
                "the expected V3 features."
            )

        print(
            "V3 model loaded successfully."
        )

        print(
            f"Isolation Forest weight: "
            f"{self.if_weight}"
        )

        print(
            f"Rule weight: "
            f"{self.rule_weight}"
        )

        print(
            f"Isolation Forest evaluation threshold (not a hybrid risk tier): "
            f"{self.risk_threshold:.4f}"
        )

        print(
            f"ISO min: "
            f"{self.iso_min:.6f}"
        )

        print(
            f"ISO max: "
            f"{self.iso_max:.6f}"
        )

        print(
            f"Expected features: "
            f"{len(ISO_FEATURES)}"
        )

    # ======================================================
    # ISOLATION FOREST SCORE
    # ======================================================

    def calculate_if_score(
        self,
        features
    ):

        X = pd.DataFrame(
            [features],
            columns=ISO_FEATURES
        )

        # Same median handling used during training
        X = X.fillna(
            self.train_medians
        )

        # Same scaler
        X_scaled = self.scaler.transform(
            X
        )

        # Same direction as V3
        #
        # V3:
        # raw_iso = -decision_function(...)
        #
        # Higher value = more anomalous
        raw_score = -self.isolation_forest.decision_function(
            X_scaled
        )[0]

        return float(
            raw_score
        )

    # ======================================================
    # V3 NORMALIZATION
    # ======================================================

    def normalize_if_score(
        self,
        raw_score
    ):

        normalized = (
            raw_score - self.iso_min
        ) / (
            self.iso_max
            - self.iso_min
            + 1e-9
        )

        return float(
            np.clip(
                normalized,
                0,
                1
            )
        )

    # ======================================================
    # SECURITY RULE ENGINE
    # ======================================================

    def calculate_rule_score(
        self,
        transaction,
        features
    ):

        score = 0.0

        reasons = []

        # --------------------------------------------------
        # Account completely drained
        # --------------------------------------------------

        account_drained = int(
            features["oldbalanceOrg"] > 0
            and
            features["newbalanceOrig"] == 0
        )

        if account_drained:

            score += 0.20

            reasons.append(
                "Account balance was fully drained"
            )

        # --------------------------------------------------
        # Destination balance is zero
        # --------------------------------------------------

        zero_destination_balance = int(
            features["newbalanceDest"] == 0
        )

        if zero_destination_balance:

            score += 0.10

            reasons.append(
                "Destination balance is zero"
            )

        # --------------------------------------------------
        # New recipient
        # --------------------------------------------------

        if features["recipient_novel"]:

            score += 0.10

            reasons.append(
                "New recipient"
            )

        # --------------------------------------------------
        # High amount / origin balance ratio
        # --------------------------------------------------

        high_amount_ratio = int(
            features[
                "amount_to_origin_balance"
            ] >= 0.90
        )

        if high_amount_ratio:

            score += 0.15

            reasons.append(
                "Transaction uses a high proportion "
                "of the available balance"
            )

        # --------------------------------------------------
        # Large transaction
        # --------------------------------------------------

        if (
            features["amount"]
            >= self.amount_threshold
        ):

            score += 0.10

            reasons.append(
                "Unusually large transaction"
            )

        # --------------------------------------------------
        # Device change
        # --------------------------------------------------

        if bool(
            transaction.get(
                "device_change",
                False
            )
        ):

            score += 0.10

            reasons.append(
                "New or changed device"
            )

        # --------------------------------------------------
        # IP / network mismatch
        # --------------------------------------------------

        if bool(
            transaction.get(
                "ip_network_mismatch",
                False
            )
        ):

            score += 0.10

            reasons.append(
                "IP/network mismatch"
            )

        # --------------------------------------------------
        # Recent SIM swap
        # --------------------------------------------------

        if bool(
            transaction.get(
                "recent_sim_swap",
                False
            )
        ):

            score += 0.10

            reasons.append(
                "Recent SIM change"
            )

        # --------------------------------------------------
        # Amount deviates from customer's historical baseline
        # --------------------------------------------------

        if bool(
            transaction.get(
                "amount_deviation",
                False
            )
        ):

            score += 0.10

            reasons.append(
                "Amount deviates sharply from the customer's "
                "usual pattern"
            )

        # --------------------------------------------------
        # Transaction timing outside usual active hours
        # --------------------------------------------------

        if bool(
            transaction.get(
                "unusual_hour",
                False
            )
        ):

            score += 0.10

            reasons.append(
                "Transaction outside the customer's usual "
                "active hours"
            )

        # --------------------------------------------------
        # Velocity spike
        # --------------------------------------------------

        if bool(
            transaction.get(
                "velocity_spike",
                False
            )
        ):

            score += 0.05

            reasons.append(
                "Unusual transaction velocity"
            )

        if bool(transaction.get("frequency_spike", False)):
            score += 0.10
            reasons.append(
                "Today's transaction frequency is above the customer's "
                "historical pattern"
            )

        return (
            min(score, 1.0),
            reasons
        )

    # ======================================================
    # FINAL TRANSACTION ASSESSMENT
    # ======================================================

    def assess_transaction(
        self,
        transaction,
        dest_previous_count
    ):

        # --------------------------------------------------
        # Build V3 features
        # --------------------------------------------------
        
        features = build_features(
          transaction,
          dest_previous_count
)

        # --------------------------------------------------
        # Isolation Forest
        # --------------------------------------------------

        raw_if_score = (
            self.calculate_if_score(
                features
            )
        )

        if_score = (
            self.normalize_if_score(
                raw_if_score
            )
        )

        # --------------------------------------------------
        # Rule engine
        # --------------------------------------------------

        rule_score, reasons = (
            self.calculate_rule_score(
                transaction,
                features
            )
        )

        # --------------------------------------------------
        # Hybrid risk score
        # --------------------------------------------------

        hybrid_score = (
            self.if_weight * if_score
            +
            self.rule_weight * rule_score
        )

        hybrid_score = float(
            np.clip(
                hybrid_score,
                0.0,
                1.0
            )
        )

        step_up_signal = any(
            bool(transaction.get(signal, False))
            for signal in (
                "device_change",
                "ip_network_mismatch",
                "recent_sim_swap",
                "velocity_spike",
                "frequency_spike",
                "amount_deviation",
                "unusual_hour",
            )
        )
        if step_up_signal:
            hybrid_score = max(hybrid_score, self.low_threshold)

        # --------------------------------------------------
        # Risk decision
        # --------------------------------------------------

        if hybrid_score < self.low_threshold:

            risk_level = "LOW"

            decision = "APPROVE"

        elif hybrid_score < self.medium_threshold:

            risk_level = "MEDIUM"

            decision = (
                "STEP-UP VERIFICATION"
            )

        else:

            risk_level = "HIGH"

            decision = (
                "TEMPORARY HOLD / BLOCK"
            )

        # --------------------------------------------------
        # Return assessment
        # --------------------------------------------------

        return {

            "risk_score": round(
                hybrid_score,
                4
            ),

            "risk_level": risk_level,

            "decision": decision,

            "isolation_forest_score": round(
                if_score,
                4
            ),

            "rule_score": round(
                rule_score,
                4
            ),

            "reasons": reasons,

            "features": features
        }


# ======================================================
    # FEATURE IMPORTANCE (model interpretability)
    # ======================================================

    def get_feature_importances(self):
        """Return ranked feature importance from the trained forest."""

        importances = None

        if hasattr(self.isolation_forest, "feature_importances_"):
            importances = self.isolation_forest.feature_importances_
        else:
            # sklearn IsolationForest does not populate feature_importances_;
            # approximate it by averaging the impurity decrease across trees.
            estimators = getattr(self.isolation_forest, "estimators_", None)
            if estimators:
                acc = np.zeros(len(ISO_FEATURES))
                for tree in estimators:
                    acc += tree.tree_.compute_feature_importances(
                        normalize=False
                    )
                acc /= len(estimators)
                total = float(acc.sum())
                if total > 0:
                    acc = acc / total
                importances = acc

        if importances is None:
            return []

        pairs = sorted(
            zip(ISO_FEATURES, np.asarray(importances).tolist()),
            key=lambda pair: pair[1],
            reverse=True
        )

        return [
            {"feature": name, "importance": round(float(value), 6)}
            for name, value in pairs
        ]


# ==========================================================
# SINGLE MODEL INSTANCE
# ==========================================================

fraud_detector = FraudDetector()