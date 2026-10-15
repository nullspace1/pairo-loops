
# Usage tracking

- As a user, I would like to track through the "Current information" tab the token usage associated with a particular session.

- More generally, I would like for each tab's information to be conformed of dynamic statistics fields that can be added an removed, with the existing set being defaults

## Construction:

- Each sidebar category has a statistic field, and the sidebar can refresh periodically every few seconds with the new statistic state. Each statisic field has a key value and a statistic() method to render. They may use event listeners to keep track of data

- The token usage must use the client's token usage events to update the statistics.

- Other 'statistics' can be fixed fields

- Use the current set of key-value pairs as defaults
