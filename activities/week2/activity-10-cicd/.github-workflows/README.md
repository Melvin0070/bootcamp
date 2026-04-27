# Workflow files (mirrored)

GitHub Actions only loads workflows from `.github/workflows/` at the
**repo root**. The two workflows that power Activity 10 live there:

- [activity-10-ci.yml](../../../../.github/workflows/activity-10-ci.yml)
- [activity-10-deploy.yml](../../../../.github/workflows/activity-10-deploy.yml)

The path filter on each workflow scopes runs to this activity's
directory so changes to siblings (Activities 7-9, 11-12) do not
trigger them.
