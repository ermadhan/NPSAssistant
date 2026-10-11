# NPS portfolio

Local Windows app for an NPS subscriber. Download the CRA transaction statement CSV from the NPS website, then load it here. The app shows current units for Tier 1 and Tier 2 and prices them with the latest NAV from [npsnav.in](https://npsnav.in/nps-api).

The importer supports the current CRA statement layout and the legacy FY 2020-21 layout with schemes arranged as columns. Newly encountered layouts are reported for header mapping rather than silently imported with missing holdings.

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
