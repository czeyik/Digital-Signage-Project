# Self-hosted OpenMapTiles basemap

The fleet map keeps GPS collection and history in the DUDU API. OpenMapTiles
only supplies the basemap. The web container serves a verified MBTiles extract
through these authenticated routes:

- `/locations/style.json` — MapLibre style
- `/locations/tiles.json` — TileJSON metadata
- `/locations/tiles/{z}/{x}/{y}.pbf` — XYZ vector tiles

The container reads the file configured by `OPENMAPTILES_MBTILES_PATH` (the
production default is `/openmaptiles/malaysia.mbtiles`). The host stores the
matching file at `/srv/duducar/openmaptiles/malaysia.mbtiles`, and Docker mounts
that directory read-only into the web container.

## Prepare an extract

Use the [OpenMapTiles generation and hosting documentation](https://openmaptiles.org/docs/)
to download a Malaysia extract or generate one from an approved OpenStreetMap
snapshot. Do not commit the MBTiles file to Git or place it in the media bucket.
Before transfer, verify that it is a valid, non-empty MBTiles database:

```sh
sqlite3 malaysia.mbtiles 'PRAGMA integrity_check; SELECT COUNT(*) FROM tiles;'
```

The integrity check must return `ok`, and the tile count must be greater than
zero. Record the SHA-256 digest with the release evidence.

## Install or update production data

Transfer the verified file through the approved SSM artifact-transfer procedure
and atomically place it as `/srv/duducar/openmaptiles/malaysia.mbtiles` with
`root:root` ownership and mode `0644`. Keep the directory `root:root` and
`0755`. Never hand-edit `/etc/duducar/host.env` or the container environment.

The web service opens the database read-only for each tile request, so an
atomic replacement does not require a restart. Keep the previous file until
the new digest and the authenticated `/locations/style.json` and tile routes
have been checked; retain or remove it only through the reviewed transfer
procedure.

The map includes OpenStreetMap and OpenMapTiles attribution. Refreshing the
extract is a separate reviewed maintenance operation because the source data,
generation time, digest, and storage/bandwidth usage must be recorded.
