# Auto-updating QSO Map for your QRZ page

QRZ blocks scripts and iframes on bio pages, so this builds your map as a
**PNG image** on GitHub's free servers every 6 hours. Your QRZ page just shows
that image, so it stays current with no work on your end.

## One-time setup (about 15 minutes)

1. **Create a GitHub account** at github.com if you don't have one.
2. **Make a new public repository** named `qso-map`.
3. **Add the files:**
   - **Add file > Upload files**: drag in `make_map.py` and the `docs` folder.
   - **Add file > Create new file**: type the name
     `.github/workflows/update-map.yml` (the slashes make the folders), paste
     in the contents of that file from this zip, and commit. (Doing this one by
     hand avoids Windows/Mac hiding the `.github` folder during upload.)
4. **Add your QRZ logbook key** as a secret:
   - QRZ: Logbook > Settings > API, copy the API key.
   - GitHub repo: Settings > Secrets and variables > Actions >
     **Secrets** tab > New repository secret.
     Name: `QRZ_API_KEY`, value: your key.
5. **Add your call and grid** on the **Variables** tab (same page):
   - `MY_CALL` = your callsign
   - `MY_GRID` = your grid square (Oak Ridge is EM85)
   - Optional: `MAP_VIEW` = `auto` (default, zooms to your contacts),
     `world`, or `na` (North America)
   - Optional: `MAP_TITLE` = any title you like
6. **Turn on GitHub Pages**: Settings > Pages > Source "Deploy from a branch",
   Branch `main`, folder `/docs`, Save.
7. **Run it once**: Actions tab > Update QSO map > Run workflow.

Your map is then at:
`https://tnemtp18325-oss.github.io/qso-map/qso-map.png`

## Add it to QRZ

Edit your Biography, switch to Source/HTML mode, and paste:

```html
<div style="text-align:center; margin:10px 0;">
  <a href="https://www.qrz.com/db/KZ5MH" >
    <img src="https://tnemtp18325-oss.github.io/qso-map/qso-map.png"
         alt="My QSO map" style="max-width:100%; border-radius:6px;">
  </a>
</div>
```

## Notes

- **QRZ subscription:** QRZ only allows reading your logbook through the API
  (the FETCH command) with a paid subscription. Without one, skip step 4 and
  instead upload an ADIF export from your logging program as `log.adi` in the
  repo. The map rebuilds automatically whenever you upload a new `log.adi`.
- **Missing contacts:** a contact is only plotted if the log has a grid square
  (or lat/lon) for it. Logs filled in from QRZ lookups usually do.
- **Refresh timing:** browsers and QRZ may cache the image for a little while,
  so a new contact can take a few hours to show.
- Each band gets its own color; your QTH is the gold star.
