# Spec Delta

## Purpose

Let users talk to ReelChat through a Telegram bot: send text, voice notes and videos, tap buttons, and receive replies and finished videos in the same chat.

## ADDED Requirements

### Requirement: Authenticated webhook
The system SHALL accept Telegram updates only on a webhook that presents the configured secret token, and SHALL process each update at most once.

#### Scenario: Valid update
- **WHEN** an update arrives with the correct secret-token header
- **THEN** it is stored, acknowledged with 200, and processed asynchronously

#### Scenario: Missing or wrong secret
- **WHEN** the header is missing or wrong
- **THEN** the system responds 401 and stores nothing

#### Scenario: Redelivered update
- **WHEN** Telegram redelivers an update with an already seen update id
- **THEN** it is acknowledged and not processed again

### Requirement: Supported inbound content
The system SHALL handle private-chat text, voice notes, video files (including videos sent as documents) and button presses, and SHALL answer anything else with a short message saying what is supported.

#### Scenario: Text and voice
- **WHEN** a user sends text or a voice note
- **THEN** it is treated as the next conversation input, voice notes being transcribed first

#### Scenario: Unsupported content or group chat
- **WHEN** a user sends a sticker, location, or writes from a group chat
- **THEN** the bot replies that it works with text, voice notes and videos in a private chat, and does not change any project

### Requirement: Buttons are safe and size-limited
The system SHALL present confirmation and action choices as inline buttons whose payload is a short opaque token bound to the user, project and current confirmation state.

#### Scenario: Confirm button
- **WHEN** the user taps the confirm button for the current plan
- **THEN** generation starts exactly once, as when the user types an explicit yes

#### Scenario: Stale or foreign button
- **WHEN** a button for an outdated plan, another user, or an unknown token is tapped
- **THEN** nothing is started and the user is told the plan has changed

### Requirement: Ordered, reliable delivery
The system SHALL send replies in order, retry transient send failures, and deliver finished videos with the Telegram video upload when they fit its limit, otherwise as a signed download link.

#### Scenario: Video within limit
- **WHEN** the finished reel is 50 MB or smaller
- **THEN** it is sent as a playable video in the chat

#### Scenario: Video over limit
- **WHEN** the reel is larger than 50 MB
- **THEN** the chat receives a signed, expiring download link instead

#### Scenario: User blocked the bot
- **WHEN** Telegram reports the user blocked the bot
- **THEN** sending stops without retry and the account shows the chat as unreachable on the website
