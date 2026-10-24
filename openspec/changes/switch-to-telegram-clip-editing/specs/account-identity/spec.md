# Spec Delta

## Purpose

Define what identifies a ReelChat account (the Telegram user ID), what is not required, and how sensitive actions such as deletion are confirmed.

## ADDED Requirements

### Requirement: Telegram ID is the identity
The system SHALL identify an account by exactly one Telegram user ID, and SHALL NOT require a password, email address or phone number.

#### Scenario: One account per Telegram user
- **WHEN** the same Telegram user logs in from several browsers or chats with the bot
- **THEN** every path reaches the same single account

#### Scenario: No password login
- **WHEN** a client attempts the former email and password endpoints
- **THEN** they are unavailable

### Requirement: Profile data minimisation
The system SHALL store only the Telegram user ID, display name, optional username and language needed for the product, and SHALL show them on the account page.

#### Scenario: Account page
- **WHEN** a signed-in user opens their account
- **THEN** they see their Telegram name and username, credits, and whether the bot can reach them

### Requirement: Re-authentication in Telegram
The system SHALL require a fresh confirmation from the user's own Telegram chat before deleting an account.

#### Scenario: Delete confirmed in the bot
- **WHEN** a user requests deletion and confirms with the button the bot sends to their chat
- **THEN** the account, conversations, clips, projects and files are deleted

#### Scenario: No or foreign confirmation
- **WHEN** the confirmation is missing, expired, or pressed from another Telegram account
- **THEN** nothing is deleted

### Requirement: Stopped or blocked bot
The system SHALL keep the account usable on the website when the user has blocked the bot, and SHALL tell them the chat is unreachable.

#### Scenario: Blocked bot
- **WHEN** Telegram reports the user blocked the bot
- **THEN** the dashboard shows a notice to restart the bot, and videos remain downloadable from the website
