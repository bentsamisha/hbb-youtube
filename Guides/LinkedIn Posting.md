# LinkedIn launch post (publish day)

Every episode gets a LinkedIn launch post from Mykhailo's own profile, about 10 minutes after the video goes live
(Monday 09:00 Lisbon video, so about 09:10), with the YouTube link in the FIRST COMMENT, never in the post.

**Format** (his own, see the ISO 42001 roles post): hook line + one-line twist, short paragraphs on what the standard
actually says, "New video goes through ...", then "🦡 N minutes. No upselling. Just what the standards say." and
"Link in the comments 👇", then 3-5 hashtags. Image: the thumbnail. First comment:
🎬 Watch the full breakdown here: <YouTube link> / 🐝 More about HoneyBadger & Bees: https://www.honeybadgerandbees.com/

**Where the text comes from**
- The editor writes three launch posts (Master Content System, Part 6) into `post.json`; the Review folder gets
  `LINKEDIN.txt` with the best one under POST, the other two as alternatives, and the first comment.
- Edit the POST section in `LINKEDIN.txt` to change the wording; your edit is kept through re-edits and the upload.

**How it gets posted**
1. Approving the episode (moving it to "3 Approved") approves its LINKEDIN.txt POST, first comment and image
   (THUMBNAIL.jpg) as they are in the folder. On upload the post joins `System/linkedin_queue.json` as `approved`,
   time = live + 10 min. Edit LINKEDIN.txt before you approve if you want other wording.
   (Set `autopilot.linkedin_approve_with_episode` to false to get a separate yes in the chat instead.)
2. Only posts still `needs_ok` get a question in the chat on Sunday evening. Nothing is posted
   without that yes for that specific post.
3. Publish day ~09:12: if the video is public, Claude posts it on linkedin.com through Claude in Chrome on the Mac,
   with the episode thumbnail attached as the image, then adds the first comment (🎬 YouTube link, 🐝 website),
   pins it, and marks the item `done` with the post's link.
   If the video isn't public yet, it waits and tries again at the next check.

**What Mykhailo needs**: Chrome on the Mac with the Claude extension connected, signed in to his own LinkedIn
(michael-bentsa). Then reply to the comments within 2 hours and don't edit the post in the first hour.
