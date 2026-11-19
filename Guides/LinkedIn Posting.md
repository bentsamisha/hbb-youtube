# LinkedIn launch post (publish day)

Every episode gets a LinkedIn launch post from Mykhailo's own profile, about 10 minutes after the video goes live
(Monday 09:00 Lisbon video, so about 09:10), with the YouTube link in the FIRST COMMENT, never in the post.

**Where the text comes from**
- The editor writes three launch posts (Master Content System, Part 6) into `post.json`; the Review folder gets
  `LINKEDIN.txt` with the best one under POST, the other two as alternatives, and the first comment.
- Edit the POST section in `LINKEDIN.txt` to change the wording; your edit is kept through re-edits and the upload.

**How it gets posted**
1. When the video uploads, the post joins `System/linkedin_queue.json` (status `needs_ok`, time = live + 10 min).
2. The evening before (Sunday ~19:40), Claude shows the exact post in the chat and asks for a yes. Nothing is posted
   without that yes for that specific post.
3. Publish day ~09:12: if the video is public, Claude posts it on linkedin.com through Claude in Chrome on the Mac,
   then adds the first comment with the YouTube link, and marks it `done` with the post's link.
   If the video isn't public yet, it waits and tries again at the next check.

**What Mykhailo needs**: Chrome on the Mac with the Claude extension connected, signed in to his own LinkedIn
(michael-bentsa). Then reply to the comments within 2 hours and don't edit the post in the first hour.
