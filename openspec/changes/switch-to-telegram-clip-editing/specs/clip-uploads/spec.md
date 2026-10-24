# Spec Delta

## Purpose

Let users give ReelChat their own video clips, from the Telegram chat or the website, and keep them safely until the reel is made.

## ADDED Requirements

### Requirement: Send clips in the chat
The system SHALL accept videos sent to the bot as videos or documents up to the bot download limit and attach them to the user's current project.

#### Scenario: Clip within the limit
- **WHEN** a user sends a video of 20 MB or less
- **THEN** it is stored, checked, attached to the current project (a new draft project if none), and the bot confirms with its length and the project's clip count

#### Scenario: Clip over the chat limit
- **WHEN** a video larger than the bot can download is sent
- **THEN** the bot replies with a short-lived website upload link for the same project instead of failing silently

### Requirement: Upload on the website
The system SHALL let a user upload clips to a project from the website, including through a signed upload link sent by the bot, without exposing other account data.

#### Scenario: Signed upload link
- **WHEN** a user opens the upload link from the bot
- **THEN** they can add clips to that one project for the link's lifetime, even if they are not signed in on that browser

#### Scenario: Expired or foreign link
- **WHEN** the link is expired or belongs to another project
- **THEN** uploading is refused

### Requirement: Validation and limits
The system SHALL accept only decodable video files and SHALL enforce configurable limits on file size, clip duration, clips per project and total storage.

#### Scenario: Not a video
- **WHEN** an uploaded file cannot be decoded as video
- **THEN** it is rejected with a clear message and nothing is stored

#### Scenario: Limit exceeded
- **WHEN** an upload would exceed a configured limit
- **THEN** it is rejected naming the limit and the existing clips are unchanged

### Requirement: Manage clips
The system SHALL let the user list, reorder and remove clips of a project on the website and by chat command, until generation starts.

#### Scenario: Remove a clip
- **WHEN** the user removes a clip from a draft project
- **THEN** it is deleted from storage and no longer appears in the plan

#### Scenario: Project already rendering
- **WHEN** the project is queued or processing
- **THEN** its clips cannot be removed or reordered

### Requirement: Privacy and retention
The system SHALL keep clips private to their owner and SHALL delete them after a configured retention period or when the user deletes them or their account.

#### Scenario: Retention elapsed
- **WHEN** a project's clips are older than the retention period
- **THEN** they are deleted and the project page says the source clips expired while the finished reel remains

#### Scenario: Another user asks for a clip
- **WHEN** a request for a clip does not come from its owner or a valid signed link
- **THEN** it is refused
