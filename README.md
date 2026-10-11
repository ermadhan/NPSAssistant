# NPS portfolio

Local Windows app for an NPS family. Download each member's CRA transaction statement CSV from the NPS website, then load it here. The app shows current units for Tier 1 and Tier 2 and prices them with the latest NAV from [npsnav.in](https://npsnav.in/nps-api).

The importer supports the current CRA statement layout and the legacy FY 2020-21 layout with schemes arranged as columns. Newly encountered layouts are reported for header mapping rather than silently imported with missing holdings.

## Portfolio views

- **Overview** shows scheme positions, current value, estimated net invested, calculated XIRR, and the XIRR reported in the latest statement.
- **Allocation** rolls each member and the family up by Tier 1/Tier 2 and Equity, Corporate, Government, and Alternative scheme categories. Current value uses the latest NAV when available and otherwise falls back to the statement NAV.
- **Statements** retains imported statement history and transactions.
- **Securities** is reserved for underlying stock and bond exposure from official pension-fund disclosures.

Calculated XIRR uses the earliest imported account valuation as the starting value, dated contribution and withdrawal transactions in the imported coverage period, and the latest available portfolio value as the ending value. Scheme-level XIRR includes recorded transfers into and out of that scheme; account-level XIRR excludes internal scheme switches. XIRR is omitted when the imported history is insufficient to determine a unique return.

Net invested starts from scheme-level net contributions reported by a statement when available, then adjusts for subsequent contributions, withdrawals, and scheme transfers. When the initial scheme balance or transaction history is missing, the app marks the estimate as partial. Statement-reported XIRR is displayed separately and is not substituted for the calculated return.

## Family accounts

Import each family member's Tier 1 and Tier 2 statements into the same local app. Accounts are identified from the subscriber PRAN in each statement (or subscriber name if a PRAN is missing), so importing a newer statement for one person and tier replaces only that account's current snapshot. The Family holdings view shows consolidated family totals and each member's separate tier breakdown. Select a member to view only that account. Historical statement views and exports retain the subscriber name.

## Run

```powershell
python -m pip install -r requirements.txt
$env:PYTHONPATH = "src"
python -m portfolio_app
```

The database is created at `%LOCALAPPDATA%\NpsPortfolio\portfolio.db`.

## Tests

```powershell
$env:PYTHONPATH = "src"
python -m unittest tests.test_nps_samples
```
