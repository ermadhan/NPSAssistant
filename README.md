# NPS portfolio

Local Windows app for an NPS subscriber. Download the CRA transaction statement CSV from the NPS website, then load it here. The app shows current units for Tier 1 and Tier 2 and prices them with the latest NAV from [npsnav.in](https://npsnav.in/nps-api).

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
