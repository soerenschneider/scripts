# vault_approle_cli

Manage Hashicorp Vault AppRoles on the CLI

## Features

✅ Supply (multiple) AppRole identities for a machine

✅ Follow best-practices and automate continuous secret_ids rotation 

✅ Supports almost all AppRole API calls

✅ Minimal [dependencies](requirements.txt) to run on almost all Linux host (`requests`is required, `iso8601`is recommended)


## Available Subcommands

| Subcommand                 | Description                                                |
|----------------------------|------------------------------------------------------------|
| rotate-secret-id           | Create a new secret-id and destroy all previous secret-ids |
| add-secret-id              | Add a new secret-id to a role                              |
| list-roles                 | List all approles by name                                  |
| get-role-id                | Get role_id for a given approle name                       |
| get-role                   | Get role information for a given approle name              |
| delete-role                | Delete an approle                                          |
| list-secret-id-accessors   | List all secret_id_accessors for a role                    |
| list-groups                | List all groups by name                                    |
| list-entities              | List all entities by name                                  |
| get-entity                 | Get an entity by name                                      |
| lookup-secret-id           | Lookup a secret_id                                         |
| login                      | Login to an approle                                        |
| destroy-secret-id          | Destroy a secret_id_accessor for a given role_name         |
| destroy-all-secret-ids     | Destroy all secret_id_accessors for a given role_name      |
| unwrap-secret-id           | Unwrap a secret_id from a token                            |

## Configuration

### Global Flags

| Name          | Description                                                                            |
|---------------|----------------------------------------------------------------------------------------|
| config        | Read the configuration from the given file                                             |
| vault-address | The address to reach vault. If not specified, uses `VAULT_ADDR` env var.               |
| vault-token   | The token to use. If not specified, uses VAULT_TOKEN env var or `~/.vault-token` file. |

### rotate-secret-id

| Name                | Description                                                                                | Default    |
|---------------------|--------------------------------------------------------------------------------------------|------------|
| role-name-json-path | JSON path to role-name                                                                     | .role_name |
| role-id-json-path   | JSON path to role-id                                                                       | .role_id   |
| secret-id-json-path | JSON path to secret-id                                                                     | .secret_id |
| metric-file         | File to write prometheus metrics to                                                        |            |
| ignore-cidr         | Ignore previously attached CIDRs                                                           |            |
| force-rotation      | Force the rotation regardless of the validity period of the secret_id                      |            |
| min-validity-period | Rotate the secret_id if the remaining validity is less than x. Value is in percent (0-100) | 34         |
| secret-id           | The secret_id to use for authentication                                                    |            |
| secret-id-file      | Flat file that contains the AppRole's secret_id                                            |            |
| secret-id-json-file | JSON encoded file that contains the AppRole's secret_id                                    |            |
| role-id             | The AppRole's role_id                                                                      |            |
| role-id-json-file   | JSON encoded file that contains the AppRole's role_id                                      |            |
| role-name           | The AppRole's role_name                                                                    |            |
| role-name-json-file | JSON encoded file that contains the AppRole's role_name                                    |            |

### add-secret-id

| Name                | Description                                                                  | Default    |
|---------------------|------------------------------------------------------------------------------|------------|
| role-name-json-path | JSON path to role-name                                                       | .role_name |
| secret-id-json-path | JSON path to secret-id                                                       | .secret_id |
| wrap-ttl            | Wraps the secret_id. Argument is specified in seconds                        | .secret_id |
| auto-limit-cidr     | Perform a DNS lookup against a host and set CIDRvalidity for token and login |            |
| limit-cidr          | Limits secret_id usage and token_usage to CIDR blocks                        | []         |
| destroy-others      | Destroys other secret_ids for this role                                      | False      |
| metadata            |                                                                              |            |
| push-secret-id      |                                                                              | False      |
| secret-id-file      | Flat file that contains the AppRole's secret_id                              |            |
| secret-id-json-file | JSON encoded file that contains the AppRole's secret_id                      |            |
| role-name           | The AppRole's role_name                                                      |            |
| role-name-json-file | JSON encoded file that contains the AppRole's role_name                      |            |
