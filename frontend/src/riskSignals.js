export function describeRiskSignal(reason) {
  const descriptions = {
    "New recipient":
      "The destination has not appeared in the customer's observed recipient history.",
    "New or changed device":
      "The device identifier differs from the customer's known device profile.",
    "IP/network mismatch":
      "The transaction came from a network outside the customer's expected range.",
    "Recent SIM change":
      "A simulated recent SIM-change signal was present for this session.",
    "Unusual transaction velocity":
      "Multiple transaction sessions occurred within a short time window.",
    "Unusually large transaction":
      "The amount crossed the configured high-value transaction threshold.",
    "Account balance was fully drained":
      "The transfer would reduce the originating balance to zero.",
    "Destination balance is zero":
      "The destination account has no recorded prior balance.",
  };

  if (descriptions[reason]) return descriptions[reason];
  if (reason.includes("usual pattern")) {
    return "The amount differs materially from the customer's observed transaction baseline.";
  }
  if (reason.includes("usual active hours")) {
    return "The transaction occurred outside the customer's configured active-hour range.";
  }
  if (reason.includes("high proportion")) {
    return "The transfer consumes a large share of the available account balance.";
  }
  if (reason.includes("frequency")) {
    return "The customer has made more transactions than usual within the observed period.";
  }
  return "This signal contributed to the automated escalation and should be checked during investigation.";
}
