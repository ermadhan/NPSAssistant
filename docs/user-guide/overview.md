# Overview

Overview is the family portfolio dashboard. It combines family totals with the separate account-holder holdings and is the main place to review scheme-level balances.

## What the screen shows

- **Current value** totals the selected holdings using the latest stored NAV where available; otherwise it uses the statement NAV.
- **Net invested** estimates contributions remaining in the portfolio. An asterisk indicates that the value is partial because the imported history does not provide a complete starting balance.
- **Calculated XIRR** estimates the annualized return from dated cash flows and portfolio value over the available history.
- **Statement XIRR** is the return reported by the statement. It is shown separately and can differ from the calculated return.
- **Account balances** lists Tier 1, Tier 2, and combined values for the family and each included account holder.
- The scheme tables below the summary show units, statement and latest NAVs, current or snapshot value, and (in the current view) available scheme-level invested amount and XIRR.

Calculated XIRR may show “Not enough history” when the imported data is insufficient to determine a reliable return. Scheme-level XIRR includes recorded transfers into and out of that scheme; account-level returns exclude internal scheme switches.

## Filter the portfolio

Use the shared Name, Tier, Scheme, and Financial year filters above the dashboard. Selecting a member shows that account alone; selecting a tier or scheme narrows totals and holdings accordingly. Selecting a financial year displays the holdings captured by statements for that year, not values repriced as of today.

When no holdings match, check whether the active filters are too restrictive. If no statements have been loaded yet, choose **Load statement** from the app-bar menu.

## Understand partial invested amounts

Net invested may be approximate if the earliest imported statement does not include scheme-level net contributions or if transaction history is incomplete. The asterisk on the summary and the partial-invested note explain when this applies. It does not indicate an error in the current-value calculation.
