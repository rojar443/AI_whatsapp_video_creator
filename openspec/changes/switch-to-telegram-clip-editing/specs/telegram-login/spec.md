# Spec Delta

## Purpose

Let a person sign in to, or create, a ReelChat account by pressing Start in the ReelChat Telegram bot from a website link, without a password or phone number.

## ADDED Requirements

### Requirement: Start a Telegram login
The system SHALL let an unauthenticated visitor request a login and SHALL return a one-time token and a `t.me` deep link that opens the bot with that token.

#### Scenario: Visitor requests login
- **WHEN** a visitor accepts the terms and requests a login
- **THEN** the system returns a deep link `https://t.me/<bot>?start=<token>` valid for at most 15 minutes

#### Scenario: Terms not accepted or rate limited
- **WHEN** terms are not accepted, or one client exceeds the allowed requests
- **THEN** the system rejects with 422 or 429 and creates no token

### Requirement: Confirm in the bot
The system SHALL confirm a login when the bot receives `/start <token>` for an open token, taking the sender's Telegram user ID from the update.

#### Scenario: Valid token
- **WHEN** a private-chat `/start <token>` arrives for an open, unexpired token
- **THEN** the token is confirmed and the bot tells the user to return to the website

#### Scenario: Unknown, expired or reused token
- **WHEN** the token is unknown, expired, or already confirmed
- **THEN** the login is not confirmed and the bot explains how to request a new link

### Requirement: Browser handoff
The system SHALL issue a session only to the browser that requested the token, exactly once, after confirmation.

#### Scenario: Requesting browser polls after confirmation
- **WHEN** the originating browser polls and the login is confirmed
- **THEN** session and CSRF cookies are set and the token cannot be redeemed again

#### Scenario: Another client polls
- **WHEN** a client without the originating browser's secret polls the login
- **THEN** no session is issued

### Requirement: Account resolution
The system SHALL sign in the account bound to the confirming Telegram user ID, and SHALL create one if none exists.

#### Scenario: Returning user
- **WHEN** the Telegram user ID already has an account
- **THEN** the browser is signed in to that account

#### Scenario: New user
- **WHEN** the Telegram user ID has no account
- **THEN** an account is created with the Telegram first name, the signup credits are granted once, and the browser is signed in

#### Scenario: Suspended account
- **WHEN** the account is suspended
- **THEN** no session is issued and the bot says the account is unavailable

### Requirement: Chat-first start
The system SHALL let a user who opens the bot directly (without a website token) start using it, and SHALL link that chat to the same account when the user later logs in on the website.

#### Scenario: Bot opened first
- **WHEN** a new user sends `/start` to the bot with no token
- **THEN** an account is created for their Telegram ID and the bot explains how to send clips and a brief

#### Scenario: Website later
- **WHEN** that user later requests a website login
- **THEN** the same account and projects are opened
