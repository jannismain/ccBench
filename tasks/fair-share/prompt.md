# Fair Share

Build a web-based cost-splitting platform for small groups named "Fair Share".

The product should help groups of people track shared expenses, calculate who owes whom, and settle balances transparently.

## Goal

Deliver a production-ready software solution for splitting expenses in groups such as trips, dinners, flat-sharing, or events.

## Required product scope

The solution must include all of the following capabilities:

1. **User accounts and authentication**
   - Users can register, sign in, and sign out securely.
   - A user has a profile with at least a display name and email address.

2. **Group management**
   - Users can create, rename, and archive groups.
   - Users can invite other users to a group.
   - Before accepting an invitation, a user can see the group name and the inviter's name.
   - Users can accept or decline group invitations.

3. **Expense management**
   - Users can add an expense to a group.
   - An expense contains at least: amount, currency, description, date, payer, and participants.
   - The split of an expense must support:
     - equal split
     - percentage-based split
     - fixed-amount split
   - Users can edit or delete an expense.

4. **Balance calculation**
   - The system calculates per-user balances within a group.
   - The system shows who owes whom.
   - The system computes a simplified settlement suggestion that minimizes the number of payments.

5. **Settlement tracking**
   - Users can record that a payment between two group members has been settled.
   - Recorded settlements affect the open balances.
   - Facilitate settlements by allowing users to enter their PayPal account name in their profile and providing a "Settle with PayPal" button that generates a PayPal payment link with the correct amount and recipient.
   - The actual payment processing can be mocked, but the system should track settlements as if they were completed.

6. **Notifications**
   - Users receive in-app notifications when:
     - they are invited to a group
     - a new expense is added in one of their groups
     - a settlement involving them is recorded

7. **User interface & experience**
   - The application must provide a browser-based UI.
   - The UI must work on desktop and mobile screen sizes.
   - The UX shall be intuitive and user-friendly, following modern design principles.
   - Where user input is required, use reasonable defaults to minimize required user actions and make entries as simple as possible.

## Delivery constraints

The solution must follow these constraints:

- Build a **web application**
- Provide at least:
  - a backend service or application server
  - a persistent data model
  - a browser-based frontend
- Authentication, notifications, and settlement recording may use simple production-reasonable implementations.
- Use an architecture style that supports modularity and separation of concerns.
- Provide a README with setup instructions and a brief architectural overview.
- Include tests that cover critical business logic and edge cases.
- Include end to end tests that validate the main user flows (e.g., creating a group, adding an expense, recording a settlement).
  - each test should have a timeout of 10 seconds to ensure they run efficiently and do not hang indefinitely.

## Acceptance criteria

A reviewer should be able to verify the following:

- A new user can register and create a group.
- A group owner can invite at least one additional member.
- The invited member can accept the invitation and join the group.
- A user can add an expense with each of the three split methods.
- Group balances update correctly after each expense.
- The system can display a simplified settlement plan.
- A recorded settlement reduces outstanding balances.
- Relevant users can see notifications in the application.
- The UI is usable on mobile and desktop layouts.
