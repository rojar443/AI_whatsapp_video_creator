# Spec Delta

## Purpose

Define how a ReelChat account is identified and reached through email + password and/or a verified WhatsApp number, and how those login methods are added and protected.

## ADDED Requirements

### Requirement: Email and password login remains available
The system SHALL continue to let a user register and sign in with email and password, with unchanged validation and rate limits.

#### Scenario: Existing email user logs in
- **WHEN** a user with an email and password submits valid credentials
- **THEN** a session is issued as before

#### Scenario: Account without a password
- **WHEN** someone tries to log in with the email of an account that has no password
- **THEN** the system responds with the same generic invalid-credentials error and takes the same code path as for an unknown email

### Requirement: Accounts may have either or both credentials
The system SHALL allow an account to exist with only a verified WhatsApp number, only email + password, or both, and SHALL never leave an account with no way to sign in.

#### Scenario: WhatsApp-only account
- **WHEN** an account is created through WhatsApp login
- **THEN** it has a verified phone and no email or password, and works normally

#### Scenario: Removing the last login method
- **WHEN** a user tries to remove their only remaining login method
- **THEN** the system refuses with a conflict error

### Requirement: Phone claimed only when verified
The system SHALL treat a phone number as claimed by an account only after the number has been verified by WhatsApp, and SHALL NOT let an unverified number block registration or sign-in of another person.

#### Scenario: Unverified duplicate number at email signup
- **WHEN** a user registers with email and a phone number that another account typed but never verified
- **THEN** registration succeeds

#### Scenario: Second account tries to verify a claimed number
- **WHEN** an account tries to link a number already verified on another account
- **THEN** the system refuses with a conflict error and changes nothing

#### Scenario: First to verify wins
- **WHEN** two accounts typed the same number and one verifies it via WhatsApp
- **THEN** the other account's number remains unverified and cannot be linked

### Requirement: Email users reach their account via WhatsApp only after verifying the number
The system SHALL sign an email-registered user in through WhatsApp login only if that user previously verified the same number by linking WhatsApp.

#### Scenario: Linked email user logs in by WhatsApp
- **WHEN** an email user who has linked their WhatsApp number completes a WhatsApp login for that number
- **THEN** they are signed in to the same account with their existing projects and credits

#### Scenario: Unlinked email user logs in by WhatsApp
- **WHEN** an email user who never linked WhatsApp completes a WhatsApp login with their typed number
- **THEN** they are not signed in to their email account; a separate WhatsApp account is created, and the email account's dashboard keeps prompting them to link WhatsApp

### Requirement: Add a login method
The system SHALL let a signed-in user add the missing login method after re-authenticating.

#### Scenario: WhatsApp-only user adds email and password
- **WHEN** a WhatsApp-only user supplies an email and a password of at least 10 characters and confirms the email
- **THEN** the account gains email + password login

#### Scenario: Email already used by another account
- **WHEN** the email belongs to a different account
- **THEN** the system refuses with a conflict error and does not merge accounts

#### Scenario: Email user links WhatsApp
- **WHEN** an email user completes the LINK flow for a number not claimed elsewhere
- **THEN** the number becomes verified and WhatsApp login becomes available

### Requirement: Re-authentication for sensitive actions
The system SHALL require a recent proof of identity before account deletion or changing login methods: the password if the account has one, otherwise a fresh WhatsApp confirmation code.

#### Scenario: Delete with password
- **WHEN** an account with a password confirms deletion with the correct password
- **THEN** the account and its data are deleted

#### Scenario: Delete a WhatsApp-only account
- **WHEN** an account without a password requests deletion
- **THEN** the system requires a fresh WhatsApp confirmation sent from the linked number before deleting

#### Scenario: Wrong or missing proof
- **WHEN** the proof is wrong, expired, or from another number
- **THEN** nothing is changed and the system returns 403
