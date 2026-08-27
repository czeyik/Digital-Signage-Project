(() => {
  const root = document.getElementById("fleet-map");
  if (!root) return;

  const latestUrl = root.dataset.latestUrl;
  const historyUrl = root.dataset.historyUrl;
  const styleUrl = root.dataset.styleUrl;
  const deviceSelect = document.getElementById("location-device");
  const startInput = document.getElementById("location-start");
  const endInput = document.getElementById("location-end");
  const historyButton = document.getElementById("location-history");
  const message = document.getElementById("location-message");
  const tableBody = document.querySelector("#location-table tbody");
  const markers = new Map();
  let map = null;
  let mapReady = false;

  const setMessage = (text) => { message.textContent = text; };

  const formatTime = (value) => value ? new Date(value).toLocaleString() : "—";

  const popup = (point) => {
    const content = document.createElement("div");
      [
        ["Device", point.device_label],
        ["Vehicle", point.vehicle_registration || "—"],
        ["Driver ID", point.driver_internal_id || "—"],
        ["Coordinates", `${point.latitude}, ${point.longitude}`],
      ["Recorded", formatTime(point.recorded_at)],
      ["Provider", point.provider],
      ["Accuracy", `${point.accuracy_m} m`],
    ].forEach(([label, value]) => {
      const row = document.createElement("div");
      row.textContent = `${label}: ${value}`;
      content.appendChild(row);
    });
    return content;
  };

  const renderRows = (devices) => {
    tableBody.replaceChildren();
    devices.forEach((device) => {
      const point = device.point;
      const row = document.createElement("tr");
      const values = [
        device.device_label,
        device.state,
        point?.vehicle_registration || "—",
        point?.driver_internal_id || "—",
        formatTime(point?.recorded_at || device.last_reported_at),
        point?.provider || "—",
        point ? `${point.accuracy_m} m` : "—",
      ];
      values.forEach((value, index) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        if (index === 1) {
          const stateClass = String(device.state || "unknown")
            .replace(/[^a-z0-9_-]/gi, "-");
          cell.className = `location-state location-state-${stateClass}`;
        }
        row.appendChild(cell);
      });
      tableBody.appendChild(row);
    });
  };

  const updateMarkers = (devices) => {
    if (!mapReady) return;
    const markerColor = (state) => {
      if (state === "planned_gap" || state === "shutdown") return "#6b7280";
      if (["unavailable", "permission_disabled", "location_disabled", "mock"].includes(state)) {
        return "#b42318";
      }
      if (state === "stale") return "#b54708";
      return "#1f6feb";
    };
    const seen = new Set();
    devices.forEach((device) => {
      if (!device.point) return;
      const id = device.device_id;
      seen.add(id);
      const point = device.point;
      let marker = markers.get(id);
      if (!marker) {
        marker = new maplibregl.Marker({ color: markerColor(device.state) })
          .setLngLat([point.longitude, point.latitude])
          .setPopup(new maplibregl.Popup({ offset: 18 }).setDOMContent(popup(point)))
          .addTo(map);
        markers.set(id, marker);
      } else {
        marker.setLngLat([point.longitude, point.latitude]);
        marker.getElement().style.backgroundColor = markerColor(device.state);
        marker.getPopup()?.setDOMContent(popup(point));
      }
    });
    markers.forEach((marker, id) => {
      if (!seen.has(id)) {
        marker.remove();
        markers.delete(id);
      }
    });
    if (seen.size && map.getZoom() < 4) {
      const bounds = new maplibregl.LngLatBounds();
      markers.forEach((marker) => bounds.extend(marker.getLngLat()));
      map.fitBounds(bounds, { padding: 48, maxZoom: 12 });
    }
  };

  const updateDevices = (devices) => {
    const previous = deviceSelect.value;
    deviceSelect.replaceChildren(new Option("Select a device", ""));
    devices.forEach((device) => {
      deviceSelect.appendChild(new Option(device.device_label, device.device_id));
    });
    if (devices.some((device) => device.device_id === previous)) deviceSelect.value = previous;
    renderRows(devices);
    updateMarkers(devices);
  };

  const refreshLatest = async () => {
    try {
      const response = await fetch(latestUrl, { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("latest location request failed");
      const payload = await response.json();
      updateDevices(payload.devices || []);
      setMessage(`Updated ${new Date().toLocaleTimeString()}.`);
    } catch (error) {
      setMessage("Location data is temporarily unavailable.");
    }
  };

  const loadHistory = async () => {
    if (!deviceSelect.value) return;
    const params = new URLSearchParams({ device_id: deviceSelect.value });
    if (startInput.value) params.set("start", new Date(startInput.value).toISOString());
    if (endInput.value) params.set("end", new Date(endInput.value).toISOString());
    try {
      const response = await fetch(`${historyUrl}?${params}`, { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("history request failed");
      const payload = await response.json();
      if (!mapReady) return;
      const coordinates = (payload.points || []).map((point) => [point.longitude, point.latitude]);
      const source = map.getSource("location-history");
      source?.setData({ type: "Feature", geometry: { type: "LineString", coordinates } });
      setMessage(`Loaded ${coordinates.length} history points.`);
    } catch (error) {
      setMessage("History is unavailable for that range.");
    }
  };

  if (!styleUrl || !window.maplibregl) {
    setMessage("Map configuration is not available.");
  } else {
    map = new maplibregl.Map({
      container: root,
      style: styleUrl,
      center: [101.6869, 3.139],
      zoom: 8,
      attributionControl: false,
      validateStyle: false,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.addControl(
      new maplibregl.AttributionControl({
        customAttribution: "© OpenStreetMap contributors · © OpenMapTiles",
      }),
      "bottom-right",
    );
    map.on("error", () => setMessage("Map tiles are temporarily unavailable."));
    map.on("load", () => {
      map.addSource("location-history", {
        type: "geojson",
        data: { type: "Feature", geometry: { type: "LineString", coordinates: [] } },
      });
      map.addLayer({
        id: "location-history-line",
        type: "line",
        source: "location-history",
        paint: { "line-color": "#d97706", "line-width": 3 },
      });
      mapReady = true;
      refreshLatest();
    });
  }

  // Keep the state table useful even when map tiles are unavailable or
  // configuration is intentionally withheld in a non-production environment.
  refreshLatest();

  deviceSelect.addEventListener("change", loadHistory);
  historyButton.addEventListener("click", loadHistory);
  window.setInterval(() => {
    if (document.visibilityState === "visible") refreshLatest();
  }, 60_000);
})();
