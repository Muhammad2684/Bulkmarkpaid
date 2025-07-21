/* ---------- DOM refs ---------- */
const input      = document.getElementById("orderInput");
const statusDiv  = document.getElementById("statusMessage");
const orderList  = document.getElementById("orderList");

/* ---------- state ---------- */
let queuedOrders = [];

/* ---------- helpers ---------- */
function showMessage(msg, type = "info") {
    statusDiv.textContent = msg;
    statusDiv.className   = `status-message ${type}`;
    statusDiv.style.display = "block";
}
function clearMessage() {
    statusDiv.textContent = "";
    statusDiv.style.display = "none";
}
function clearAll() {
    input.value      = "";
    orderList.innerHTML = "";
    queuedOrders     = [];
    clearMessage();

    document.getElementById("orderListHeader").style.display = "none";
    document.getElementById("orderTotal").style.display = "none";
    document.getElementById("totalAmount").textContent = "0.00";
}
function renderOrderToList(order, index) {
    const li = document.createElement("li");
    li.dataset.id = order.order_id;
    li.style.display = "flex";
    li.style.alignItems = "center";
    li.style.gap = "10px";

    li.innerHTML = `
        <div style="width: 40px; text-align: left;"><strong>${index}</strong></div>
        <div style="flex-grow: 1; text-align: left;">${order.order_name}</div>
        <div style="min-width: 120px; text-align: right;">Rs. ${parseFloat(order.total_price).toFixed(2)}</div>
    `;

    orderList.appendChild(li);
}
function updateTotalAmount() {
    const total = queuedOrders.reduce((sum, o) => sum + parseFloat(o.total_price || 0), 0);
    document.getElementById("totalAmount").textContent = total.toFixed(2);
}

/* ---------- core: fetch & vet an order ---------- */
async function processOrderId(id) {
    id = id.trim();
    if (!id) return Promise.resolve("skip");

    if (queuedOrders.find(o => o.order_name === id || o.order_id === id))
        return Promise.resolve("dup");

    try {
        const res  = await fetch(`/api/get_order/${id}`);
        const data = await res.json();

        if (!res.ok) return showMessage(data.error || "Load failed", "error"), "fail";

        const hasPaidTag = (data.tags || "")
            .split(",").map(t => t.trim().toLowerCase())
            .includes("paid");

        if (hasPaidTag)
            return showMessage(`${data.order_name} already has “Paid” tag.`, "error"), "tagged";

        if (data.payment_status === "paid")
            return showMessage(`${data.order_name} is already paid in Shopify.`, "error"), "paid";

        queuedOrders.push({
            order_id   : data.order_id,
            order_name : data.order_name,
            city       : data.city,
            total_price: data.total_price
        });

        renderOrderToList(data, queuedOrders.length);
        document.getElementById("orderListHeader").style.display = "flex";
        document.getElementById("orderTotal").style.display = "block";
        updateTotalAmount();

        return "ok";
    } catch (err) {
        console.error(err);
        showMessage("Server error while loading order.", "error");
        return "fail";
    }
}

/* ---------- manual “Add Order” button ---------- */
async function addOrder(orderNumber = null) {
    const id = orderNumber ? orderNumber.trim() : input.value.trim();
    if (!id) return showMessage("Please enter an order ID", "error");

    showMessage("Loading order...", "info");

    try {
        const res = await fetch(`/api/get_order/${encodeURIComponent(id)}`);
        const data = await res.json();

        if (!res.ok)
            return showMessage(data.error || "Failed to load order", "error");

        if (data.payment_status === "paid")
            return showMessage(`${data.order_name} is already marked as paid.`, "error");

        if ((data.tags || "").toLowerCase().includes("paid"))
            return showMessage(`Order ${data.order_name} is already tagged as Paid.`, "error");

        if (queuedOrders.find(o => o.order_id === data.order_id))
            return showMessage(`${data.order_name} is already added.`, "error");

        queuedOrders.push({
            order_id: data.order_id,
            order_name: data.order_name,
            city: data.city,
            total_price: data.total_price
        });

        renderOrderToList(data, queuedOrders.length);
        if (!orderNumber) input.value = "";
        clearMessage();

        document.getElementById("orderListHeader").style.display = "flex";
        document.getElementById("orderTotal").style.display = "block";
        updateTotalAmount();
    } catch (e) {
        console.error(e);
        showMessage("Error loading order", "error");
    }
}

/* ---------- CSV Upload Handler ---------- */
function handleCsvUpload(file) {
    const reader = new FileReader();
    reader.onload = function (e) {
        const csvText = e.target.result;
        const lines = csvText.split('\n').map(line => line.trim()).filter(line => line);
        const orderNumbers = lines.map(line => line.replace(/^#/, ''));

        const modal = new bootstrap.Modal(document.getElementById('csvModal'));
        modal.show();

        const listContainer = document.getElementById('csv-list');
        listContainer.innerHTML = '';

        let validCount = 0;
        let skippedCount = 0;
        const summary = document.getElementById('csv-summary');
        summary.textContent = `Processing ${orderNumbers.length} orders...`;
        summary.style.display = 'block';

        orderNumbers.forEach(async (orderNumber, index) => {
            const li = document.createElement('li');
            li.innerHTML = `<strong>#${index + 1} - ${orderNumber}</strong> — <span style="color: gray;">Checking...</span>`;
            listContainer.appendChild(li);

            try {
                const res = await fetch(`/api/get_order/${orderNumber}`);
                const data = await res.json();

                const alreadyInList = queuedOrders.some(q => q.order_name === orderNumber || q.order_id == orderNumber);

                let valid = true;
                let reason = '';

                if (!res.ok) {
                    valid = false;
                    reason = data.error || "Load failed";
                } else if (data.payment_status === "paid") {
                    valid = false;
                    reason = "Already paid";
                } else if ((data.tags || '').toLowerCase().includes('paid')) {
                    valid = false;
                    reason = "Already tagged Paid";
                } else if (alreadyInList) {
                    valid = false;
                    reason = "Already in list";
                }

                if (valid) {
                    validCount++;
                    li.dataset.valid = "true";
                    li.dataset.orderNumber = orderNumber;
                    li.querySelector("span").textContent = "✅ Valid";
                    li.querySelector("span").style.color = "lightgreen";
                } else {
                    skippedCount++;
                    li.dataset.valid = "false";
                    li.dataset.orderNumber = orderNumber;
                    li.querySelector("span").textContent = `❌ ${reason}`;
                    li.querySelector("span").style.color = "red";
                    li.style.opacity = "0.6";
                }

                summary.textContent = `${validCount} valid ✅, ${skippedCount} skipped ❌`;
            } catch (err) {
                skippedCount++;
                li.querySelector("span").textContent = "❌ Error";
                li.querySelector("span").style.color = "red";
                summary.textContent = `${validCount} valid ✅, ${skippedCount} skipped ❌`;
            }
        });
    };
    reader.readAsText(file);
    document.getElementById('csvInput').value = '';  // allow re-upload of same file
}

/* ---------- CSV Confirm Button ---------- */
document.getElementById('confirmCsvOrders').addEventListener('click', () => {
    const rows = document.querySelectorAll('#csv-list li');
    rows.forEach(row => {
        if (row.dataset.valid === 'true') {
            const orderNumber = row.dataset.orderNumber;
            addOrder(orderNumber);
        }
    });

    const modalEl = document.getElementById('csvModal');
    const modal = bootstrap.Modal.getInstance(modalEl);
    modal.hide();
});

/* ---------- mark ALL queued orders with “Paid” tag and show status inside <li> ---------- */
async function markAllAsPaid() {
    if (!queuedOrders.length)
        return showMessage("Queue is empty.", "error");

    showMessage("Tagging orders...", "info");

    for (let i = 0; i < queuedOrders.length; i++) {
        const order = queuedOrders[i];
        const li = document.querySelector(`li[data-id="${order.order_id}"]`);
        if (!li) continue;

        const statusSpan = document.createElement("span");
        statusSpan.style.marginLeft = "10px";
        statusSpan.style.fontStyle = "italic";
        statusSpan.style.color = "gray";
        statusSpan.textContent = "⏳ Tagging...";
        li.appendChild(statusSpan);

        try {
            const res = await fetch("/api/tag_order", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ order_id: order.order_id })
            });

            const data = await res.json();

            if (data.success) {
                statusSpan.textContent = "✅ Tagged Paid";
                statusSpan.style.color = "white";
                li.style.backgroundColor = "#28a745";  // Bootstrap green
                li.style.color = "white";
            } else {
                statusSpan.textContent = `❌ ${data.message || "Failed"}`;
                statusSpan.style.color = "white";
                li.style.backgroundColor = "#dc3545";  // Bootstrap red
                li.style.color = "white";
            }

        } catch (err) {
            statusSpan.textContent = "❌ Request error";
            statusSpan.style.color = "red";
            li.style.opacity = "0.6";
        }
    }

    showMessage("Finished tagging.", "success");

    // Clear queue and UI
    queuedOrders = [];
    updateTotalAmount();
}
