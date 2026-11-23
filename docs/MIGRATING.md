# Moving from "Media Pruner" to Phlexerr (one-time)

1. Stop the old `media-pruner` container.
2. Rename `appdata/media-pruner` to `appdata/phlexerr` (your `data` folder moves with it).
3. Download `Phlexerr.xml` into `/boot/config/plugins/dockerMan/templates-user/` (see the README for the one-line `wget` command), add the container from the template, then remove the old one.
4. From then on, updates are: drop `phlexerr.zip` into `appdata/phlexerr` and run `bash update.sh`.
