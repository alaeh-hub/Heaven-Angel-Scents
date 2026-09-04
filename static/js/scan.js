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

    if (!video || !canvas) return;

    var ctx = canvas.getContext("2d", { willReadFrequently: true });
    var stream = null;
    var scanning = true;
    var rafId = null;
    var lastCode = null;
    var lastCodeAt = 0;
    var devices = [];
    var deviceIndex = 0;

    function setStatus(text) {
        statusEl.textContent = text;
    }

    function hasCamera() {
        return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    }

    function stopCamera() {
        if (rafId) {
            cancelAnimationFrame(rafId);
            rafId = null;
        }
        if (stream) {
            stream.getTracks().forEach(function (track) { track.stop(); });
            stream = null;
        }
    }

    function startCamera(deviceId) {
        if (!hasCamera()) {
            setStatus("This browser doesn't support camera access here \u2014 use upload or manual entry below.");
            return;
        }
        stopCamera();
        var constraints = {
            video: deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: "environment" } },
            audio: false,
        };
        navigator.mediaDevices.getUserMedia(constraints).then(function (mediaStream) {
            stream = mediaStream;
            video.srcObject = stream;
            return video.play();
        }).then(function () {
            setStatus("Point the camera at the receipt\u2019s QR code.");
            scanning = true;
            toggleBtn.textContent = "Pause";
            tick();
            return navigator.mediaDevices.enumerateDevices();
        }).then(function (allDevices) {
            if (!allDevices) return;
            devices = allDevices.filter(function (d) { return d.kind === "videoinput"; });
            switchBtn.style.display = devices.length > 1 ? "" : "none";
        }).catch(function () {
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
        scanning = !scanning;
        toggleBtn.textContent = scanning ? "Pause" : "Resume";
        if (scanning) tick();
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

    function showMatch(sale) {
        resultEmpty.style.display = "none";
        resultCard.style.display = "";
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
    }

    function showNotFound(message) {
        resultEmpty.style.display = "none";
        resultCard.style.display = "";
        resultHead.innerHTML = '<span class="scan-badge-bad">&#10007; Not verified</span>';
        resultBody.innerHTML = '<div class="scan-field-full">' + esc(message) + "</div>";
    }

    // ---- Lifecycle ----
    document.addEventListener("visibilitychange", function () {
        if (document.hidden) {
            stopCamera();
        } else if (scanning) {
            startCamera(devices[deviceIndex] ? devices[deviceIndex].deviceId : undefined);
        }
    });

    startCamera();
})();