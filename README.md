# NPS portfolio

Local Windows app for an NPS family. Download each member's CRA transaction statement CSV from the NPS website, then load it here. The app shows current units for Tier 1 and Tier 2 and prices them with the latest NAV from [npsnav.in](https://npsnav.in/nps-api).

The importer supports the current CRA statement layout and the legacy FY 2020-21 layout with schemes arranged as columns. Newly encountered layouts are reported for header mapping rather than silently imported with missing holdings.

## Portfolio views

- **User guide:** [Screen-by-screen instructions](docs/user-guide/README.md).
- **Overview** shows scheme positions, current value, estimated net invested, calculated XIRR, and the XIRR reported in the latest statement.
- **Allocation** rolls each member and the family up by Tier 1/Tier 2 and Equity, Corporate, Government, and Alternative scheme categories. Current value uses the latest NAV when available and otherwise falls back to the statement NAV.
- **Statements** retains imported statement history and transactions.
- **Securities** estimates stock, government-bond, corporate-bond, and money-market exposure from the latest supported official pension-fund portfolio report. It combines each disclosure's scheme weights with the current value of the family's scheme holdings.

Overview, Allocation, Securities, and statement history share compact dropdown filters for member name, tier, scheme, and financial year. Selecting a financial year shows the matching statement snapshots; the securities view clearly notes that its disclosure weights may be from a different date. XIRRs are summarized together, and per-scheme values use a single calculated/reported XIRR column.

The Overview uses a family-and-account balance summary, while Allocation combines current value and invested amount in compact asset-class cells to reduce horizontal scrolling. Statement history is presented as a full-width table, long account and scheme names are bounded with tooltips, and portfolio actions are grouped in the app-bar menu.

UI currency values use Indian numbering: rupee amounts below one lakh use Indian comma grouping, values from one lakh are shown in lakh, and values from one crore are shown in crore. Exported data retains its numeric values.

### Security look-through coverage

The Securities view supports official Tier 1 and Tier 2 E, C, and G portfolio disclosures from ICICI, HDFC, and LIC Pension Funds. Use the manager-specific refresh buttons to fetch their latest published reports. The report date, covered portfolio value, and unsupported schemes are shown so the view does not imply full coverage when some imported schemes are not supported. The app stores the downloaded security rows locally and applies the disclosure's security-level portfolio weight to each scheme's current value, using a stored latest NAV when available and otherwise the statement NAV. These are estimates, not security positions held directly in an individual subscriber account.

Official sources: [NPS Trust scheme portfolio directory](https://npstrust.org.in/disclosures), [ICICI Pension Fund portfolio details](https://www.icicipension.in/public_disclosure/portfolio_details), [HDFC Pension Fund public disclosures](https://www.hdfcpension.com/public-disclosures/), and [LIC Pension Fund portfolio disclosures](https://www.licpensionfund.in/portfolio-disclosures). Other pension fund managers and non-E/C/G schemes are not yet integrated and appear as uncovered.

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

The database is created at `%LOCALAPPDATA%\NpsPortfolio\portfolio.db`. Security disclosures are stored locally in the same database.

## Tests

```powershell
$env:PYTHONPATH = "src"
python -m unittest tests.test_nps_samples
```
