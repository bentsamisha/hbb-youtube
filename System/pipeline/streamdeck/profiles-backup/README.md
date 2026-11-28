# Stream Deck profiles backup

A copy of `~/Library/Application Support/com.elgato.StreamDeck/ProfilesV3` (all pages: general, Elgato Studio
prompter, Trust No Pack, HoneyBadger and Bees). Checked for secrets before committing: none in it (only key layouts,
app paths and button texts). Last updated: 28 Nov 2026, after the HBB page and the Next Page key on Trust No Pack.

## Restore
1. Quit Stream Deck (it overwrites the folder while running).
2. Move the current `ProfilesV3` aside, then copy `ProfilesV3` from here into
   `~/Library/Application Support/com.elgato.StreamDeck/`.
3. Make sure the plugins are installed: `streamdeck/com.honeybadgerbees.deck.sdPlugin` (this repo) and
   `com.trustnopack.deck.sdPlugin` (TNP repo) in `~/Library/Application Support/com.elgato.StreamDeck/Plugins/`.
4. Open Stream Deck.

## Refresh this backup
    rsync -a --delete --exclude .DS_Store ~/Library/Application\ Support/com.elgato.StreamDeck/ProfilesV3/ \
      System/pipeline/streamdeck/profiles-backup/ProfilesV3/
