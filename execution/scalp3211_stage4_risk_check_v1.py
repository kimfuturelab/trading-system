"""Read-only risk consistency for Stage4 one-share trade plans."""


def risk_errors(row, *, cash, risk_limit):
    price = row.get("planned_entry_price")
    stop = row.get("stop_price")
    investment = row.get("planned_invest_amount")
    loss = row.get("expected_loss")
    values = (price, stop, investment, loss, cash, risk_limit)
    if any(type(n) is not int for n in values):
        return ["MISSING_INTEGER_VALUES"]
    errors = []
    if not (0 < stop < price and investment == price and loss == price - stop):
        errors.append("PRICE_STOP_LOSS_MISMATCH")
    if price > cash or loss > risk_limit:
        errors.append("INSUFFICIENT_CASH_OR_RISK_BUDGET")
    return errors
