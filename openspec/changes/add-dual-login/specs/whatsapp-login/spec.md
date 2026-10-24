# Spec Delta

## Purpose

Let a person sign in to, or create, a ReelChat account by proving they control a WhatsApp number, without a password and without the platform sending any paid outbound template message.

## ADDED Requirements

### Requirement: Start a WhatsApp login
The system SHALL let an unauthenticated visitor request a login code for a phone number in international format, and SHALL return a one-time code, the business number to send it to, and an expiry.

#### Scenario: Valid number
- **WHEN** a visitor submits a valid phone number and accepts the terms
- **THEN** the system returns a 6-digit code, a `wa.me` link prefilled with `LOGIN <code>`, and an expiry of at most 15 minutes

#### Scenario: Invalid number or terms not accepted
- **WHEN** the number is not valid E.164 or the terms are not accepted
- **THEN** the system rejects the request with a validation error and creates no code

#### Scenario: Rate limited
- **WHEN** one client requests more than the allowed number of codes in the rate-limit window
- **THEN** the system rejects further requests with 429

### Requirement: Confirm by WhatsApp message
The system SHALL confirm a login code only when the message `LOGIN <code>` is received from the same WhatsApp number the code was issued for.

#### Scenario: Correct sender and code
- **WHEN** a message `LOGIN <code>` arrives from the number the code was issued for, before expiry
- **THEN** the login is marked confirmed and the sender receives a reply saying they can return to the website

#### Scenario: Code sent from a different number
- **WHEN** the code is sent from a number other than the one requested
- **THEN** the login is not confirmed and the sender is told the code did not work

#### Scenario: Expired, wrong or exhausted code
- **WHEN** the code is expired, wrong or already used, or 5 wrong attempts were made
- **THEN** the login is not confirmed and the sender is told to request a new code on the website

### Requirement: Browser handoff
The system SHALL issue a session only to the browser that requested the code, exactly once, after the login is confirmed.

#### Scenario: Requesting browser polls after confirmation
- **WHEN** the browser that started the login polls and the login is confirmed
- **THEN** the system sets the session and CSRF cookies and the login cannot be redeemed again

#### Scenario: Another party knows the login id
- **WHEN** a client without the secret issued to the originating browser polls that login
- **THEN** the system does not return a session

#### Scenario: Not confirmed yet or expired
- **WHEN** the browser polls before confirmation or after expiry
- **THEN** the system reports `pending` or `expired` and sets no session

### Requirement: Account resolution on confirmation
The system SHALL sign in the account that has the confirmed number verified, and SHALL create a new account when none does.

#### Scenario: Number already verified on an account
- **WHEN** a login is confirmed for a number linked to an existing active account
- **THEN** the browser is signed in to that account

#### Scenario: First time number
- **WHEN** a login is confirmed for a number not verified on any account
- **THEN** a new account is created with the number verified and linked, the default signup credits are granted once, and the browser is signed in

#### Scenario: Number only typed in by an email account
- **WHEN** a login is confirmed for a number that appears only as an unverified phone on an email account
- **THEN** the system does not use that account and resolves as a first-time number

#### Scenario: Suspended account
- **WHEN** a login is confirmed for a suspended account
- **THEN** no session is issued and the sender is told the account is unavailable

### Requirement: Login works for unlinked and linked senders
The system SHALL accept the `LOGIN` command from any sender, whether or not their WhatsApp number is already linked to an account.

#### Scenario: Linked user sends LOGIN
- **WHEN** an account's linked number sends `LOGIN <code>` that matches an open login
- **THEN** the login is confirmed instead of the message being handled as video-chat input
