"""Read-only validation for the existing 3211 Stage4 plan."""


def valid_scope(row):
    return (str(row.get('account_alias', '')).lower() == 'test'
            and row.get('symbol') == '005930'
            and row.get('planned_qty') == 1)
