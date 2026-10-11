# Statement import and review

Import each family member's Tier 1 and Tier 2 CRA transaction statement CSV separately. The app stores data locally and identifies an account by its PRAN, or by subscriber name when a PRAN is unavailable.

## Load a CSV

1. Open the app-bar menu and select **Load statement**.
2. Choose the NPS transaction statement CSV downloaded from the CRA/NPS website.
3. Review the statement identity, tier, financial year, statement date, holdings total, and parsed row counts before saving.
4. Check the holdings table for the expected schemes, units, NAVs, and values.
5. Resolve any warnings or prompts, then choose **Save statement**.

Importing a newer statement for the same account and tier updates that account's current snapshot. Other family members and tiers remain separate. The imported statement remains available in Statements history.

## Confirm a year mismatch

If the filename's year disagrees with the dates parsed from the statement, the review screen displays both years and requires you to check the confirmation box before saving. The app saves using the dates in the statement.

## Map unfamiliar statement headers

If the file uses an unfamiliar CRA layout, the app asks you to map the required fields to the available column headers. Select a header for every requested field and save the mapping. The file is then read again; review the newly parsed result before saving the statement. Do not save if the holdings or totals do not match the source statement.

## Classify unfamiliar narration text

For new transaction narration phrases, choose the closest available kind for each phrase. These classifications are reused the next time the same narration appears. Contributions and withdrawals affect cash-flow analysis; opening and closing balances are kept separate from scheme-movement cash-flow totals.

If the review reports an error, correct the source file or mappings as appropriate and load it again. Review the account name and tier carefully before saving to avoid assigning a statement to the wrong account.
