# Spec Delta

## Purpose

Let a person sign in to the ReelChat website with their Telegram identity, by pressing Start in the bot after opening a one-time link or scanning its QR code, without a password or email.

## ADDED Requirements

### Requirement: Start a Telegram login
The system SHALL let an unauthenticated visitor request a login and SHALL return a one-time deep link to the bot and an expiry.

#### Scenario: Visitor requests login
- **WHEN** a visitor accepts the terms and requests a login
- **THEN** the system returns `https://t.me/<bot>?start=login_<token>` valid for at most 15 minutes

#### Scenario: Terms not accepted or rate limited
- **WHEN** terms are not accepted, or one client exceeds the allowed requests
- **THEN** the system answers 422 or 429 and creates no token

#### Scenario: Telegram not configured
- **WHEN** no bot is configured on the server
- **THEN** the system answers 503 and the login page explains that Telegram login is unavailable

### Requirement: Login by QR code
The system SHALL show the same login link as a QR code so a visitor on a computer can scan it with their phone.

#### Scenario: Scan from a computer
- **WHEN** a visitor scans the QR with their phone and presses Start in the bot
- **THEN** the computer's browser that displayed the QR is signed in

#### Scenario: Token no longer valid
- **WHEN** the token expires or has been used
- **THEN** the page replaces the QR with an expired state and a retry button

### Requirement: Confirm in the bot
The system SHALL confirm a login when the bot receives `/start login_<token>` in a private chat for an open token, identifying the user by that Telegram chat.

#### Scenario: Valid token
- **WHEN** the private-chat message arrives for an open, unexpired token
- **THEN** the token is confirmed and the bot tells the user to return to the website

#### Scenario: Unknown, expired or reused token
- **WHEN** the token is unknown, expired, or already confirmed
- **THEN** nothing is confirmed and the bot explains how to get a new link

### Requirement: Browser handoff
The system SHALL issue a web session only to the browser that requested the token, exactly once, after confirmation.

#### Scenario: Requesting browser polls
- **WHEN** the browser that started the login polls after confirmation
- **THEN** session and CSRF cookies are set and the token cannot be redeemed again

#### Scenario: Another client polls
- **WHEN** a client without the originating browser's secret polls the login
- **THEN** no session is issued

### Requirement: Account resolution
The system SHALL sign the browser in as the user bound to the confirming Telegram chat, creating that user if the chat has none, and SHALL promote an anonymous profile to a regular account while keeping its projects and credits.

#### Scenario: Chat that only used the bot
- **WHEN** an anonymous Telegram profile completes a login
- **THEN** it becomes a regular account with registered quotas and retention, its reels appear on the dashboard, and its credit balance is at least the registered default

#### Scenario: Chat linked to an email account
- **WHEN** a chat already linked to an email account completes a login
- **THEN** the browser is signed in as that email account

#### Scenario: Suspended account
- **WHEN** the resolved user is suspended
- **THEN** no session is issued and the bot says the account is unavailable

### Requirement: Registration stays optional
The system SHALL keep email registration controlled by its existing setting and SHALL NOT require an email, password or phone number to use the website through Telegram.

#### Scenario: Registration disabled
- **WHEN** registration is disabled
- **THEN** the website offers only Telegram login to new visitors
