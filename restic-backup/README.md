## Metrics Overview

| Name                                 | Help                                          | Labels | Type  |
|--------------------------------------|-----------------------------------------------|--------|-------|
| restic_backup_files_new_total        | New files created with this snapshot          | repo   | gauge |
| restic_backup_files_changed_total    | Files changed with this snapshot              | repo   | gauge |
| restic_backup_files_unmodified_total | Amount of unmodified files with this snapshot | repo   | gauge |
| restic_backup_dirs_new_total         | Newly created directories with this snapshot  | repo   | gauge |
| restic_backup_dirs_changed_total     | Changed directories with this snapshot        | repo   | gauge |
| restic_backup_dirs_unmodified_total  | Unmodified directories with this snapshot     | repo   | gauge |
| restic_backup_data_blobs_total       | Data blobs of the snapshot (?)                | repo   | gauge |
| restic_backup_tree_blobs_total       | Tree blobs of the snapshot (?)                | repo   | gauge |
| restic_backup_data_added_bytes_total | Total bytes added during this snapshot        | repo   | gauge |
| restic_backup_total_files_processed  | Files processed with this snapshot            | repo   | gauge |
| restic_backup_total_bytes_processed  | Bytes processed with this snapshot            | repo   | gauge |
| restic_backup_total_duration_seconds | Total duration of this snapshot               | repo   | gauge |
| restic_backup_success_bool           | Boolean indicating the success of the backup  | repo   | gauge |
| restic_backup_start_time_seconds     | Start time of the backup process              | repo   | gauge |
| restic_backup_exporter_errors_bool   | Exporter errors unrelated to restic           | repo   | gauge |
