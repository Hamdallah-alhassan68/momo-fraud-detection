import numpy as np


ISO_FEATURES = [
    "amount",
    "log_amount",
    "oldbalanceOrg",
    "newbalanceOrig",
    "oldbalanceDest",
    "newbalanceDest",
    "errorBalanceOrig",
    "errorBalanceDest",
    "amount_to_origin_balance",
    "amount_to_dest_balance",
    "dest_previous_count",
    "recipient_novel",
    "is_transfer",
    "is_cashout",
]


def build_features(
    transaction,
    dest_previous_count
):
    """
    Build the exact 14 features expected by V3.
    """

    amount = float(
        transaction.get("amount", 0)
    )

    oldbalance_org = float(
        transaction.get(
            "old_balance_origin",
            0
        )
    )

    newbalance_orig = float(
        transaction.get(
            "new_balance_origin",
            0
        )
    )

    oldbalance_dest = float(
        transaction.get(
            "old_balance_destination",
            0
        )
    )

    newbalance_dest = float(
        transaction.get(
            "new_balance_destination",
            0
        )
    )

    transaction_type = str(
        transaction.get(
            "transaction_type",
            "TRANSFER"
        )
    ).upper()

    # ----------------------------------------------
    # Balance errors
    # ----------------------------------------------

    error_balance_orig = (
        oldbalance_org
        - amount
        - newbalance_orig
    )

    error_balance_dest = (
        oldbalance_dest
        + amount
        - newbalance_dest
    )

    # ----------------------------------------------
    # Ratios
    # ----------------------------------------------

    if oldbalance_org > 0:

        amount_to_origin_balance = (
            amount / oldbalance_org
        )

    else:

        amount_to_origin_balance = 0.0

    if oldbalance_dest > 0:

        amount_to_dest_balance = (
            amount / oldbalance_dest
        )

    else:

        amount_to_dest_balance = 0.0

    # ----------------------------------------------
    # Recipient novelty
    # ----------------------------------------------

    recipient_novel = int(
        dest_previous_count == 0
    )

    # ----------------------------------------------
    # Transaction type
    # ----------------------------------------------

    is_transfer = int(
        transaction_type == "TRANSFER"
    )

    is_cashout = int(
        transaction_type == "CASH_OUT"
    )

    return {

        "amount": amount,

        "log_amount":
            np.log1p(amount),

        "oldbalanceOrg":
            oldbalance_org,

        "newbalanceOrig":
            newbalance_orig,

        "oldbalanceDest":
            oldbalance_dest,

        "newbalanceDest":
            newbalance_dest,

        "errorBalanceOrig":
            error_balance_orig,

        "errorBalanceDest":
            error_balance_dest,

        "amount_to_origin_balance":
            amount_to_origin_balance,

        "amount_to_dest_balance":
            amount_to_dest_balance,

        "dest_previous_count":
            int(dest_previous_count),

        "recipient_novel":
            recipient_novel,

        "is_transfer":
            is_transfer,

        "is_cashout":
            is_cashout
    }