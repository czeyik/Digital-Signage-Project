(() => {
  const list = document.querySelector("[data-playlist-sortable]");
  const form = document.querySelector("[data-order-form]");
  const orderInput = document.querySelector("[data-order-value]");

  if (!list || !form || !orderInput) {
    return;
  }

  let dragging = null;

  const playlistItems = () => [...list.querySelectorAll("[data-item-id]")];

  const updateOrderInput = () => {
    orderInput.value = playlistItems()
      .map(item => item.dataset.itemId)
      .join(",");
  };

  const finishDrag = () => {
    if (dragging) {
      dragging.classList.remove("is-dragging");
    }
    dragging = null;
    updateOrderInput();
  };

  playlistItems().forEach(item => {
    item.draggable = true;
    item.addEventListener("dragstart", event => {
      dragging = item;
      item.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", item.dataset.itemId);
    });
    item.addEventListener("dragend", finishDrag);
  });

  list.addEventListener("dragover", event => {
    if (!dragging) {
      return;
    }

    const target = event.target.closest("[data-item-id]");
    if (!target || target === dragging || target.parentElement !== list) {
      return;
    }

    event.preventDefault();
    event.dataTransfer.dropEffect = "move";

    const targetBox = target.getBoundingClientRect();
    const insertBeforeTarget = event.clientY < targetBox.top + targetBox.height / 2;
    const referenceNode = insertBeforeTarget ? target : target.nextElementSibling;

    if (referenceNode !== dragging) {
      list.insertBefore(dragging, referenceNode);
      updateOrderInput();
    }
  });

  list.addEventListener("drop", event => {
    if (dragging) {
      event.preventDefault();
    }
    finishDrag();
  });

  form.addEventListener("submit", updateOrderInput);
  updateOrderInput();
})();
