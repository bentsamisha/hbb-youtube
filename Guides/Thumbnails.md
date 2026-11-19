# Thumbnails (made in ChatGPT)

Every episode's thumbnail is generated in Mykhailo's ChatGPT thumbnail chat (it already has his photos and the
earlier thumbnails, so the face and style stay the same). The link is in `System/pipeline/config.json` → `thumbnail.chat_url`.

**How it works**
1. When a Review package is built, the folder gets `THUMBNAIL_PROMPT.txt` (made from the episode's `thumbnail_brief`
   and the house style in config.json) and the episode joins `System/thumbnail_queue.json` (status `todo`).
2. Claude, through Claude in Chrome on the Mac, opens the chat, sends the prompt, waits for the image, downloads it
   and saves it in the Review folder as `THUMBNAIL.jpg` (1280x720, under 2 MB). The queue item becomes `done`.
3. You approve it together with the video. `THUMBNAIL.jpg` is the YouTube thumbnail and the LinkedIn image.
   `cover.jpg` (a video frame with text) is only the fallback when there is no THUMBNAIL.jpg.

**Want a different one?** Write it in CHANGES.txt ("thumbnail: make the headline ..."), or edit THUMBNAIL_PROMPT.txt
and delete THUMBNAIL.jpg. You can also drop your own THUMBNAIL.jpg/.png into the folder; it wins.

**What Mykhailo needs**: the Chrome window with the Claude extension signed in to ChatGPT (the account that owns the chat).
