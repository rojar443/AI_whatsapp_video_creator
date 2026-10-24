# Spec Delta

## Purpose

Give the product owner an admin-only area that shows every person who has used the app, from any channel, and lets them manage access and credits.

## ADDED Requirements

### Requirement: Admin-only access
The system SHALL restrict the admin tab and every admin API to users with the admin role.

#### Scenario: Normal user
- **WHEN** a signed-in non-admin opens the admin page or calls an admin endpoint
- **THEN** the API answers 403 and the nav shows no Admin tab

#### Scenario: Admin signed in
- **WHEN** an admin is signed in
- **THEN** the nav shows an Admin tab that opens the user list

### Requirement: Grant admin by Telegram ID
The system SHALL treat a user as admin when their Telegram ID is in the configured admin list, applied when they log in or message the bot.

#### Scenario: Configured admin logs in
- **WHEN** a Telegram user whose ID is in the admin list completes Telegram login
- **THEN** their account has the admin role

#### Scenario: Not configured
- **WHEN** the admin list is empty
- **THEN** no one gains admin through this mechanism and existing admin roles are unchanged

### Requirement: List everyone who used the app
The system SHALL list all users who have used the app, including anonymous Telegram and WhatsApp profiles that never registered, newest activity first, with search and paging.

#### Scenario: Bot-only user appears
- **WHEN** someone has messaged the bot but never used the website
- **THEN** they appear in the list with their channel, display name or username, and activity

#### Scenario: Search and paging
- **WHEN** the admin searches by name, Telegram username or email, or requests the next page
- **THEN** matching users are returned in pages without loading the whole table

### Requirement: Per-user activity
The system SHALL show for each user their channel(s), first and last seen, number of projects and finished reels, credit balance, role and status.

#### Scenario: Active user
- **WHEN** a user has made two reels
- **THEN** the list shows two finished reels, their last activity time and their credits

### Requirement: Manage users
The system SHALL let an admin suspend or reactivate a user and adjust their credits, and SHALL record each action in the audit log.

#### Scenario: Suspend
- **WHEN** an admin suspends a user
- **THEN** the user's web sessions stop working, the bot declines new work for them, and an audit entry is written

#### Scenario: Cannot lock out self
- **WHEN** an admin tries to suspend or demote themselves
- **THEN** the request is refused

#### Scenario: Credit adjustment
- **WHEN** an admin adds or removes credits
- **THEN** the balance changes, never below zero, and a ledger entry and audit entry are written

### Requirement: Privacy of the admin view
The system SHALL show admins only account and usage data, and SHALL NOT expose message contents or uploaded media in the user list.

#### Scenario: User row
- **WHEN** the list is displayed
- **THEN** it contains no conversation text or media links
