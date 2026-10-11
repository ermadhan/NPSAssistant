# Statements

The Statements screen lists imported statement snapshots. It lets you inspect the holdings and transaction rows captured from a specific file.

## Find a statement

Use the shared Name, Tier, Scheme, and Financial year filters to narrow the history. The table shows the account holder, tier, financial year, and statement “as of” date. Select **Open** on a row to inspect it.

Use **Back to portfolio** to leave the selected statement detail. The statement detail includes:

- Holdings from that particular import, valued at the statement NAV.
- Scheme movements parsed from the statement, with a cash-flow total that excludes opening and closing balances.
- The transaction date, scheme, narration kind, description, amount, and units for each movement.
- Scheme-code matching controls for holdings that could not be matched to a NAV scheme code, when a NAV catalog is available.

Statement detail is a historical record, not a live portfolio view. To return to current holdings, navigate to Overview.

## Export statements and transactions

The app-bar menu's **Export holdings** and **Export transactions** options open a save dialog for CSV or Excel output. When launched from statement detail, the selected statement is exported. From the portfolio screens, the current member filter is applied when one is selected; otherwise the export includes the family data. Exported amounts remain numeric.

## No statements found

If the list is empty, load a CRA transaction statement CSV using **Load statement** in the app-bar menu. If the list is unexpectedly empty after filtering, reset the filters to All members, All tiers, All schemes, and Latest. In the Statements list, Latest means all imported years are shown.
