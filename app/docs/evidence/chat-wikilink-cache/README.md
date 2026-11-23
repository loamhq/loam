# Chat wiki-links with inline images (parse cache isolation)

These captures verify the fix for `public/markdown.js`'s `parse()` cache when a
reply's text is parsed without linking (`imageRefs()` for replies with inline
images, or `strip()` for thread-list and activity previews) before `markdown()`
renders the reply with `onLink`.

- **Before (`main`):** the reply's wiki-link renders as raw brackets
  (`[[Notes/page.html|Friday review]]`) with zero `a.filelink` nodes.
- **After:** the wiki-link renders as a clickable `Friday review` link alongside
  the inline image.
- **Opened:** clicking (1440 px) opens the HTML preview in the Side panel;
  tapping (390 px) opens it in Files.

| Width | Mode | Before | After | Opened |
| --- | --- | --- | --- | --- |
| 390 px phone | Light | [before](before-390-light.webp) | [after](after-390-light.webp) | [opened](opened-390-light.webp) |
| 390 px phone | Dark | [before](before-390-dark.webp) | [after](after-390-dark.webp) | [opened](opened-390-dark.webp) |
| 1440 px desktop | Light | [before](before-1440-light.webp) | [after](after-1440-light.webp) | [opened](opened-1440-light.webp) |
| 1440 px desktop | Dark | [before](before-1440-dark.webp) | [after](after-1440-dark.webp) | [opened](opened-1440-dark.webp) |

Reproduce with `node test/chat-wikilink-cache-browser.mjs` from `app` with
`LOAM_PLAYWRIGHT` and `LOAM_CHROME` set (`WIKILINK_CACHE_EVIDENCE` writes the
frames; `WIKILINK_CACHE_BASELINE=1` asserts the pre-fix regression).
