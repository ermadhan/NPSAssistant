# NPS Family Portfolio user guide

This guide explains the four main screens and the statement-import review flow:

- [Overview](overview.md) — view family and member balances, scheme holdings, and returns.
- [Allocation](allocation.md) — compare Tier 1 and Tier 2 values across asset classes.
- [Securities](securities.md) — estimate the underlying stocks and bonds represented by supported pension-fund schemes.
- [Statements](statements.md) — find imported statements and inspect statement holdings and transactions.
- [Statement import and review](statement-import-review.md) — load, validate, and save a CRA transaction statement.

## Controls available throughout the app

Use the navigation rail on the left to switch between Overview, Allocation, Securities, and Statements. The app-bar menu at the top right contains:

- **Load statement** to select a downloaded NPS transaction statement CSV.
- **Refresh NAVs** to fetch available NAVs and match fund schemes.
- **Export holdings** to save holdings as CSV or Excel.
- **Export transactions** to save transaction rows as CSV or Excel.

Overview, Allocation, Securities, and the statement-history list share four filters:

- **Name** limits the view to one account holder, or all family members.
- **Tier** selects Tier 1, Tier 2, or both.
- **Scheme** limits the view to one pension-fund scheme.
- **Financial year** selects the current/latest portfolio view or a historical statement snapshot. In Statements, leaving it at **Latest** shows the full imported history.

Changing a filter updates the current screen. Choosing a historical financial year shows statement values rather than current values. The Securities screen uses the latest stored disclosure weights even when viewing a historical statement, so its note calls out that the dates may differ.

## Reading amounts

UI currency is displayed in Indian numbering. Amounts below Rs 1 lakh use Indian comma grouping (for example, `Rs 12,345.67`); amounts from Rs 1 lakh are displayed in lakh, and amounts from Rs 1 crore in crore. Values in exported files remain numeric and are not converted to lakh or crore text.

Long account, scheme, and security names may be shortened to fit the screen. Hover over the text to see the full value.
