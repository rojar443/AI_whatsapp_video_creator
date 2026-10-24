# Spec Delta

## Purpose

Turn a user's uploaded clips and a short description of the story they want into a finished vertical reel, with an explicit plan and confirmation before any credits are spent.

## ADDED Requirements

### Requirement: Brief gathering
The system SHALL ask only for what is missing from the user's brief (story or vibe, platform, length), using sensible defaults, and SHALL treat the clips already sent as given.

#### Scenario: Brief in the first message
- **WHEN** the user sends clips and says "travel reel, cinematic adventure, 30 seconds for Instagram"
- **THEN** the bot proceeds straight to the plan without further questions

#### Scenario: Missing detail
- **WHEN** length or platform is missing
- **THEN** the bot asks one short question for it and offers a default

### Requirement: Plan before spending
The system SHALL show a plan listing the clips used, vibe, length, music and credit cost, and SHALL start rendering only after an explicit confirmation by button or a short explicit yes.

#### Scenario: Confirm
- **WHEN** the user confirms the current plan
- **THEN** one render job is queued and the credits are reserved

#### Scenario: Plan changes
- **WHEN** the user changes the brief or the clips after the plan is shown
- **THEN** the old confirmation becomes invalid and a new plan is shown

#### Scenario: Not enough credits or no clips
- **WHEN** credits are insufficient or the project has no clips
- **THEN** nothing is queued and the bot says what is needed

### Requirement: Reel from the user's footage
The system SHALL produce an MP4 in the requested vertical format whose picture comes only from the user's clips, cut to the requested length.

#### Scenario: Output format and length
- **WHEN** a render completes
- **THEN** the file is a playable vertical H.264 MP4 within a small tolerance of the requested length

#### Scenario: Landscape or rotated source
- **WHEN** a clip is landscape or has rotation metadata
- **THEN** it is fitted to the vertical frame without distortion

#### Scenario: Short or few clips
- **WHEN** the clips total less than the requested length
- **THEN** the reel uses all of them and the bot tells the user the final length

### Requirement: Moment selection and pacing
The system SHALL choose and order segments from the clips to match the requested pace, preferring varied, sharp, well-exposed segments and keeping upload order unless the user asks otherwise.

#### Scenario: Pace
- **WHEN** the user asks for a faster pace
- **THEN** segments in the next revision are shorter on average than in the previous one

#### Scenario: Run without special hardware
- **WHEN** a render runs on a machine with no GPU
- **THEN** it completes using only CPU processing

### Requirement: Music, captions and transitions
The system SHALL add music matching the requested vibe, optional captions or title text, and transitions between segments, with the original clip audio mixed under or muted according to the plan.

#### Scenario: Music chosen
- **WHEN** the plan says cinematic music
- **THEN** the reel contains a music track at a level that does not drown original speech

#### Scenario: Captions off
- **WHEN** the user turns captions off
- **THEN** no on-screen text is burned in

### Requirement: Revisions reuse the clips
The system SHALL let the user request changes to a delivered reel and SHALL re-edit from the same source clips as a new revision with a new confirmation.

#### Scenario: Change request
- **WHEN** the user says "make the opening faster and end on the sunset"
- **THEN** the bot updates the brief, shows a new plan, and on confirmation renders revision n+1 without asking for the clips again

#### Scenario: Failure
- **WHEN** a render fails
- **THEN** reserved credits are refunded, the user is told, and a retry does not charge twice

### Requirement: Delivery
The system SHALL send the finished reel to the Telegram chat and make it available on the website project page.

#### Scenario: Ready
- **WHEN** a render completes
- **THEN** the chat receives the reel and the dashboard shows it with the clips it was made from
