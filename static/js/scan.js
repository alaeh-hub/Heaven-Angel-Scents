(function () {
    "use strict";

    var video = document.getElementById("scanVideo");
    var canvas = document.getElementById("scanCanvas");
    var statusEl = document.getElementById("scanCameraStatus");
    var toggleBtn = document.getElementById("scanCameraToggle");
    var switchBtn = document.getElementById("scanCameraSwitch");
    var drop = document.getElementById("scanDrop");
    var fileInput = document.getElementById("scanFileInput");
    var manualInput = document.getElementById("scanManualInput");
    var manualBtn = document.getElementById("scanManualBtn");
    var resultCard = document.getElementById("scanResultCard");
    var resultHead = document.getElementById("scanResultHead");
    var resultBody = document.getElementById("scanResultBody");
    var resultEmpty = document.getElementById("scanResultEmpty");
    var actionsEl = document.getElementById("scanActions");

    if (!video || !canvas) return;

    var ctx = canvas.getContext("2d", { willReadFrequently: true });
    var stream = null;
    var scanning = true;
    var cameraOn = false;
    var rafId = null;
    var lastCode = null;
    var lastCodeAt = 0;
    var devices = [];
    var deviceIndex = 0;
    var cameraOffOverlay = document.getElementById("scanCameraOff");

    function setStatus(text) {
        statusEl.textContent = text;
    }

    function hasCamera() {
        return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    }

    // Fully releases the camera (stops every track, so the browser/OS
    // camera-in-use indicator turns off too) rather than just pausing
    // frame scanning — that's what the on/off button now does; see
    // toggleBtn's click handler below.
    function stopCamera() {
        if (rafId) {
            cancelAnimationFrame(rafId);
            rafId = null;
        }
        if (stream) {
            stream.getTracks().forEach(function (track) { track.stop(); });
            stream = null;
        }
        video.srcObject = null;
        cameraOn = false;
        if (cameraOffOverlay) cameraOffOverlay.style.display = "";
        switchBtn.style.display = "none";
        toggleBtn.textContent = "Start camera";
        toggleBtn.classList.remove("btn-ghost");
        toggleBtn.classList.add("btn-accent");
        setStatus("Camera is off \u2014 tap Start camera to scan.");
    }

    function startCamera(deviceId) {
        if (!hasCamera()) {
            setStatus("This browser doesn't support camera access here \u2014 use upload or manual entry below.");
            return;
        }
        stopCamera();
        setStatus("Starting camera\u2026");
        var constraints = {
            video: deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: "environment" } },
            audio: false,
        };
        navigator.mediaDevices.getUserMedia(constraints).then(function (mediaStream) {
            stream = mediaStream;
            video.srcObject = stream;
            return video.play();
        }).then(function () {
            cameraOn = true;
            if (cameraOffOverlay) cameraOffOverlay.style.display = "none";
            setStatus("Point the camera at the receipt\u2019s QR code.");
            scanning = true;
            toggleBtn.textContent = "Stop camera";
            toggleBtn.classList.remove("btn-accent");
            toggleBtn.classList.add("btn-ghost");
            tick();
            return navigator.mediaDevices.enumerateDevices();
        }).then(function (allDevices) {
            if (!allDevices) return;
            devices = allDevices.filter(function (d) { return d.kind === "videoinput"; });
            switchBtn.style.display = (cameraOn && devices.length > 1) ? "" : "none";
        }).catch(function () {
            stopCamera();
            setStatus("Camera access was blocked or unavailable \u2014 use upload or manual entry below.");
        });
    }

    function tick() {
        if (!scanning || !stream) return;
        if (video.readyState === video.HAVE_ENOUGH_DATA && typeof jsQR === "function") {
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            var imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
            var code = jsQR(imageData.data, imageData.width, imageData.height, { inversionAttempts: "dontInvert" });
            if (code && code.data) {
                var now = Date.now();
                if (code.data !== lastCode || now - lastCodeAt > 4000) {
                    lastCode = code.data;
                    lastCodeAt = now;
                    verifyCode(code.data);
                }
            }
        }
        rafId = requestAnimationFrame(tick);
    }

    toggleBtn.addEventListener("click", function () {
        if (cameraOn) {
            stopCamera();
        } else {
            startCamera(devices[deviceIndex] ? devices[deviceIndex].deviceId : undefined);
        }
    });

    switchBtn.addEventListener("click", function () {
        if (!devices.length) return;
        deviceIndex = (deviceIndex + 1) % devices.length;
        startCamera(devices[deviceIndex].deviceId);
    });

    // ---- Upload a photo/screenshot ----
    drop.addEventListener("click", function () { fileInput.click(); });
    ["dragover", "dragenter"].forEach(function (evt) {
        drop.addEventListener(evt, function (e) {
            e.preventDefault();
            drop.classList.add("is-dragover");
        });
    });
    ["dragleave", "drop"].forEach(function (evt) {
        drop.addEventListener(evt, function (e) {
            e.preventDefault();
            drop.classList.remove("is-dragover");
        });
    });
    drop.addEventListener("drop", function (e) {
        var file = e.dataTransfer.files && e.dataTransfer.files[0];
        if (file) handleFile(file);
    });
    fileInput.addEventListener("change", function () {
        if (fileInput.files && fileInput.files[0]) handleFile(fileInput.files[0]);
    });

    function handleFile(file) {
        if (typeof jsQR !== "function") {
            showNotFound("The QR reader didn't load \u2014 refresh the page and try again.");
            return;
        }
        var objectUrl = URL.createObjectURL(file);
        var img = new Image();
        img.onload = function () {
            canvas.width = img.naturalWidth;
            canvas.height = img.naturalHeight;
            ctx.drawImage(img, 0, 0);
            URL.revokeObjectURL(objectUrl);
            var imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
            var code = jsQR(imageData.data, imageData.width, imageData.height);
            if (code && code.data) {
                verifyCode(code.data);
            } else {
                showNotFound("Couldn\u2019t find a QR code in that image \u2014 try a clearer or closer photo.");
            }
        };
        img.onerror = function () {
            URL.revokeObjectURL(objectUrl);
            showNotFound("That file couldn\u2019t be read as an image.");
        };
        img.src = objectUrl;
    }

    // ---- Manual code entry ----
    manualBtn.addEventListener("click", function () {
        var value = manualInput.value.trim();
        if (value) verifyCode(value);
    });
    manualInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
            e.preventDefault();
            manualBtn.click();
        }
    });

    // ---- Shared verify call + result rendering ----
    function verifyCode(code) {
        // getCsrfToken() is main.js's shared helper (reads the same
        // meta[name="csrf-token"] tag base.html renders) — main.js loads
        // before this script, see base.html's script order.
        fetch("/scan/verify", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCsrfToken(),
            },
            body: JSON.stringify({ code: code }),
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.match) {
                    showMatch(data.sale);
                } else {
                    showNotFound(data.message || "That code doesn\u2019t match any receipt on file.");
                }
            })
            .catch(function () {
                showNotFound("Couldn\u2019t reach the server to verify that code.");
            });
    }

    function field(label, valueHtml, full) {
        return (
            '<div' + (full ? ' class="scan-field-full"' : '') + '>' +
            '<div class="scan-field-label">' + label + "</div>" + valueHtml + "</div>"
        );
    }

    function esc(value) {
        var div = document.createElement("div");
        div.textContent = String(value);
        return div.innerHTML;
    }

    function peso(value) {
        return "\u20b1" + Number(value).toLocaleString("en-PH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function popResult(ok) {
        resultCard.classList.remove("scan-pop-ok", "scan-pop-bad");
        // Force a reflow so re-adding the class restarts the CSS
        // animation even when the card was already open from a
        // previous scan (back-to-back camera hits, for example).
        void resultCard.offsetWidth;
        resultCard.classList.add(ok ? "scan-pop-ok" : "scan-pop-bad");
    }

    function showMatch(sale) {
        resultEmpty.style.display = "none";
        resultCard.style.display = "";
        popResult(true);
        if (window.Sfx) window.Sfx.play("scanOk");
        resultHead.innerHTML =
            '<span class="scan-badge-ok">&#10003; Verified &mdash; on file</span>' +
            '<span class="mono text-soft" style="margin-left:auto;font-size:12px;">' + esc(sale.receipt_no) + "</span>";

        var rows = [
            field("Item", esc(sale.item_name) + ' <span class="text-soft mono" style="font-size:11px;">(' + esc(sale.sku) + ")</span>"),
            field("Variant / Unit", esc(sale.variant) + " &middot; " + esc(sale.unit)),
            field("Quantity", esc(sale.qty_sold)),
            field("Unit price", esc(peso(sale.unit_price))),
            field("Total", esc(peso(sale.line_total))),
            field("Sale type", esc(sale.sale_type)),
            field("Payment", esc(sale.payment_method)),
            field("Customer", esc(sale.customer_name)),
            field("Branch", esc(sale.branch_name)),
            field("Date", esc(sale.sold_at)),
        ];
        if (sale.customer_address) {
            rows.push(field("Address", esc(sale.customer_address), true));
        }
        resultBody.innerHTML = rows.join("");
        renderActions(sale);
    }

    function showNotFound(message) {
        resultEmpty.style.display = "none";
        resultCard.style.display = "";
        popResult(false);
        if (window.Sfx) window.Sfx.play("scanBad");
        resultHead.innerHTML = '<span class="scan-badge-bad">&#10007; Not verified</span>';
        resultBody.innerHTML = '<div class="scan-field-full">' + esc(message) + "</div>";
        actionsEl.style.display = "none";
        actionsEl.innerHTML = "";
    }

    // ---- Void / mark-credit-paid actions on a verified sale ----
    function renderActions(sale) {
        actionsEl.innerHTML = "";
        actionsEl.style.display = "";

        var feedback = document.createElement("div");
        feedback.className = "scan-action-feedback";
        feedback.id = "scanActionFeedback";
        actionsEl.appendChild(feedback);

        var row = document.createElement("div");
        row.className = "scan-actions-row";

        if (sale.is_credit && !sale.credit_settled) {
            var settleBtn = document.createElement("button");
            settleBtn.type = "button";
            settleBtn.className = "btn btn-accent btn-sm";
            settleBtn.textContent = "Mark credit paid";
            settleBtn.addEventListener("click", function () { settleCredit(sale, settleBtn); });
            row.appendChild(settleBtn);
        }

        var voidBtn = document.createElement("button");
        voidBtn.type = "button";
        voidBtn.className = "btn btn-ghost btn-sm";
        voidBtn.textContent = "Void sale";
        if (!sale.voidable) {
            voidBtn.disabled = true;
            voidBtn.title = "Branch staff can only void sales recorded today — ask HQ to void older ones.";
        } else {
            voidBtn.addEventListener("click", function () { showVoidPrompt(sale); });
        }
        row.appendChild(voidBtn);

        actionsEl.appendChild(row);
    }

    function setActionFeedback(ok, message) {
        var fb = document.getElementById("scanActionFeedback");
        if (!fb) return;
        fb.textContent = message;
        fb.className = "scan-action-feedback " + (ok ? "ok" : "bad");
    }

    function disableActionButtons() {
        actionsEl.querySelectorAll(".scan-actions-row button").forEach(function (b) { b.disabled = true; });
    }

    function showVoidPrompt(sale) {
        var existing = document.getElementById("scanVoidPrompt");
        if (existing) existing.remove();

        var wrap = document.createElement("div");
        wrap.className = "scan-void-prompt";
        wrap.id = "scanVoidPrompt";
        wrap.innerHTML =
            '<label for="scanVoidReasonInput" style="font-size:12px;font-weight:700;">Reason for voiding</label>' +
            '<textarea id="scanVoidReasonInput" rows="2" maxlength="255" ' +
            'placeholder="e.g. Wrong item scanned, customer returned it, entered twice"></textarea>' +
            '<div style="display:flex;gap:8px;">' +
            '<button type="button" class="btn btn-ghost btn-sm" id="scanVoidCancelBtn">Cancel</button>' +
            '<button type="button" class="btn btn-danger btn-sm" id="scanVoidConfirmBtn">Confirm void</button>' +
            "</div>";
        actionsEl.appendChild(wrap);
        document.getElementById("scanVoidReasonInput").focus();
        document.getElementById("scanVoidCancelBtn").addEventListener("click", function () { wrap.remove(); });
        document.getElementById("scanVoidConfirmBtn").addEventListener("click", function () {
            var reason = document.getElementById("scanVoidReasonInput").value.trim();
            if (!reason) {
                setActionFeedback(false, "Give a reason for voiding this sale.");
                return;
            }
            voidSale(sale.sale_id, reason, wrap);
        });
    }

    function voidSale(saleId, reason, promptEl) {
        fetch("/scan/sales/" + saleId + "/void", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
            body: JSON.stringify({ reason: reason }),
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.ok) {
                    promptEl.remove();
                    setActionFeedback(true, data.message);
                    disableActionButtons();
                } else {
                    setActionFeedback(false, data.message || "Couldn’t void that sale.");
                }
            })
            .catch(function () {
                setActionFeedback(false, "Couldn’t reach the server to void that sale.");
            });
    }

    function settleCredit(sale, btn) {
        btn.disabled = true;
        fetch("/scan/sales/" + sale.sale_id + "/settle-credit", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
            body: JSON.stringify({}),
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.ok) {
                    sale.credit_settled = true;
                    setActionFeedback(true, data.message);
                    btn.remove();
                } else {
                    btn.disabled = false;
                    setActionFeedback(false, data.message || "Couldn’t mark that credit as paid.");
                }
            })
            .catch(function () {
                btn.disabled = false;
                setActionFeedback(false, "Couldn’t reach the server to mark that credit as paid.");
            });
    }

    // ---- Lifecycle ----
    // The camera no longer starts on page load (see below) — it only
    // ever turns on when the person taps "Start camera". So on tab
    // refocus, only resume it if it was actually left on before the tab
    // was hidden; a fresh/never-started page should stay off.
    var wasOnBeforeHidden = false;
    document.addEventListener("visibilitychange", function () {
        if (document.hidden) {
            wasOnBeforeHidden = cameraOn;
            stopCamera();
        } else if (wasOnBeforeHidden) {
            startCamera(devices[deviceIndex] ? devices[deviceIndex].deviceId : undefined);
        }
    });

    // Deliberately no startCamera() call here — the camera stays off
    // until the person explicitly presses "Start camera" above.
    stopCamera();
})();