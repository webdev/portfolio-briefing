# Google Sheets Setup Guide

## Sheet Sharing Configuration

The skill requires that the Google Sheet be shared "anyone with the link can view" so the public gviz CSV endpoint is accessible without authentication.

### How to configure sheet sharing

1. Open your Google Sheet in a browser
2. Click the **Share** button (top right)
3. In the sharing dialog:
   - Click the access level dropdown (currently "Restricted" or similar)
   - Select **"Viewer"** (allows viewing but not editing)
   - In the "Link sharing" section, ensure **"Anyone with the link"** is selected
   - Click **Copy link** to get the shareable URL
4. Save the sheet

### Verifying access

Once shared, the public gviz endpoint should be accessible:

```
https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv
```

Test access by:
1. Copy the URL into a browser
2. The browser should download a CSV file (not show an error or HTML)

If you see HTML instead of CSV, the sheet is not yet publicly shared. Re-check the sharing settings.

## URL Format

The full gviz endpoint URL has this structure:

```
https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv
```

Where `{SHEET_ID}` is extracted from the sheet's URL:

```
https://docs.google.com/spreadsheets/d/12Fs_d8Zr4sKnoCxb5EaEbe2FciXIGPVTFGM9iehZq3M/edit#gid=0
                                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                       This is the SHEET_ID
```

## Why this approach?

Google Drive MCP and OAuth would be alternatives, but:

- **Google Drive MCP** has quota limits and sometimes returns "Item not found" on Shared Drive files
- **OAuth** adds complexity and requires user configuration
- **gviz public endpoint** is simple, public, and doesn't consume API quota

The public endpoint is the same mechanism wheelhouz uses, so it's battle-tested.

## Troubleshooting

### "HTML response instead of CSV"

The sheet is not publicly shared. Go back to Step 1 and ensure "Anyone with the link" is selected.

### "403 Forbidden"

The sheet exists but is not publicly shared. Same fix as above.

### "404 Not Found"

The sheet ID in the URL is incorrect. Copy the full URL from your browser's address bar and verify the sheet ID is correct.

### Empty sheet response

The sheet is shared but has no data. Verify:
- Data is in columns A-G starting at row 2
- Header row is present at row 1
- No hidden rows/columns are filtering the view

## Security Note

By making the sheet publicly viewable, anyone with the URL can see the stock recommendations. This is acceptable if:

- You're comfortable sharing your stock picks publicly
- The sheet contains no sensitive personal information beyond the picks themselves

If you're concerned about privacy, you can:

1. Create a separate, public version of the sheet with just the recommendations
2. Keep your main analysis sheet private
3. Copy recommendations to the public version regularly

The skill will work with either approach.
