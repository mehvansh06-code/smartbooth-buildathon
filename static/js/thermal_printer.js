/**
 * thermal_printer.js - 58mm Portable Thermal Printer (HOP-HL58) Engine for SmartBooth
 * 
 * Features:
 * - 1-Tap Web Bluetooth Direct Printing for Android Mobile (Chrome) & Laptops
 * - Canvas 2D Devanagari (Hindi) High-Contrast Rasterizer (384px / 48mm at 203 DPI)
 * - Strictly Official Slip Format (Matching State Election / ECI Slip)
 * - Auto-reconnect & Persistent Bluetooth Connection
 * - Fallback 58mm System Print for USB/Laptops
 */

(function () {
  'use strict';

  // State
  let bluetoothDevice = null;
  let gattServer = null;
  let writeCharacteristic = null;
  let isConnecting = false;
  let isPrinting = false;

  const THERMAL_WIDTH = 384; // 384 dots for 58mm thermal paper (48mm printable area)

  // Standard POS Thermal Printer Service & Characteristic UUIDs
  const KNOWN_SERVICE_UUIDS = [
    '000018f0-0000-1000-8000-00805f9b34fb', // Common POS thermal printer service
    '0000ffe0-0000-1000-8000-00805f9b34fb', // Standard BLE serial service
    'e7810a71-73ae-499d-8c15-faa9aef0c3f2', // Rongta / POS-58 service
    '49535343-fe7d-4ae5-8fa9-9fafd205e455', // ISSC transparent transmission
    '0000ff00-0000-1000-8000-00805f9b34fb', // Generic POS
    '0000fff0-0000-1000-8000-00805f9b34fb'  // OEM POS
  ];

  // =========================================================================
  // 1. BLUETOOTH CONNECTION MANAGER
  // =========================================================================

  function isBluetoothSupported() {
    return !!(navigator.bluetooth && navigator.bluetooth.requestDevice);
  }

  function updateStatusUI(connected, deviceName) {
    const badges = document.querySelectorAll('.thermal-printer-status-badge');
    badges.forEach(badge => {
      if (connected) {
        badge.className = 'thermal-printer-status-badge inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-black bg-emerald-100 text-emerald-800 border border-emerald-300';
        badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span><span>${deviceName || 'HOP-HL58'} कनेक्टेड</span>`;
      } else {
        badge.className = 'thermal-printer-status-badge inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-100 text-slate-600 border border-slate-300';
        badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-slate-400"></span><span>प्रिंटर कनेक्ट नहीं है</span>`;
      }
    });
  }

  async function connectBluetoothPrinter() {
    if (!isBluetoothSupported()) {
      alert('आपके ब्राउज़र में Web Bluetooth समर्थित नहीं है। कृपया Android पर Google Chrome का उपयोग करें।');
      return null;
    }

    if (isConnecting) return null;
    isConnecting = true;

    try {
      if (window.showToast) window.showToast('🔍 ब्लूटूथ प्रिंटर खोज रहे हैं...', 'info', 2500);

      const device = await navigator.bluetooth.requestDevice({
        acceptAllDevices: true,
        optionalServices: KNOWN_SERVICE_UUIDS
      });

      bluetoothDevice = device;
      device.addEventListener('gattserverdisconnected', onDeviceDisconnected);

      if (window.showToast) window.showToast(`प्रिंटर ${device.name || 'HOP-HL58'} से जुड़ रहे हैं...`, 'info', 2500);

      const server = await device.gatt.connect();
      gattServer = server;

      // Find write characteristic
      const char = await resolveWriteCharacteristic(server);
      writeCharacteristic = char;

      const devName = device.name || 'HOP-HL58';
      localStorage.setItem('sb_thermal_printer_name', devName);
      updateStatusUI(true, devName);

      if (window.showToast) window.showToast(`✓ प्रिंटर ${devName} सफलतापूर्वक कनेक्ट हो गया!`, 'success', 3000);
      return char;
    } catch (err) {
      console.warn('Bluetooth connection cancelled or failed:', err);
      if (err.name !== 'NotFoundError') { // User didn't just cancel the dialog
        if (window.showToast) window.showToast('प्रिंटर कनेक्ट नहीं हो सका: ' + (err.message || 'Error'), 'error', 4000);
      }
      updateStatusUI(false);
      return null;
    } finally {
      isConnecting = false;
    }
  }

  async function resolveWriteCharacteristic(server) {
    for (const uuid of KNOWN_SERVICE_UUIDS) {
      try {
        const service = await server.getPrimaryService(uuid);
        const chars = await service.getCharacteristics();
        for (const c of chars) {
          if (c.properties.write || c.properties.writeWithoutResponse) {
            return c;
          }
        }
      } catch (e) {
        // service not present, continue
      }
    }

    // Generic discovery
    try {
      const services = await server.getPrimaryServices();
      for (const service of services) {
        const chars = await service.getCharacteristics();
        for (const c of chars) {
          if (c.properties.write || c.properties.writeWithoutResponse) {
            return c;
          }
        }
      }
    } catch (e) {}

    throw new Error('प्रिंटर पर डेटा लिखने की कैरेक्टरिस्टिक नहीं मिली।');
  }

  function onDeviceDisconnected() {
    console.warn('Thermal printer disconnected');
    writeCharacteristic = null;
    gattServer = null;
    updateStatusUI(false);
    if (window.showToast) window.showToast('प्रिंटर डिस्कनेक्ट हो गया', 'warning', 2500);
  }

  async function getActiveCharacteristic() {
    if (writeCharacteristic && gattServer && gattServer.connected) {
      return writeCharacteristic;
    }

    // PWA Silent Auto-Reconnect: If already paired previously, connect without prompt
    if (navigator.bluetooth && navigator.bluetooth.getDevices) {
      try {
        const devices = await navigator.bluetooth.getDevices();
        if (devices && devices.length > 0) {
          const savedName = localStorage.getItem('sb_thermal_printer_name');
          const dev = devices.find(d => d.name === savedName) || devices[0];
          if (dev) {
            bluetoothDevice = dev;
            dev.addEventListener('gattserverdisconnected', onDeviceDisconnected);
            const server = await dev.gatt.connect();
            gattServer = server;
            const char = await resolveWriteCharacteristic(server);
            writeCharacteristic = char;
            const devName = dev.name || 'HOP-HL58';
            updateStatusUI(true, devName);
            return char;
          }
        }
      } catch (e) {
        // Fallback to interactive requestDevice below
      }
    }

    return await connectBluetoothPrinter();
  }

  // =========================================================================
  // 2. CANVAS 2D DEVANAGARI SLIP GENERATOR (Strictly Official Format)
  // =========================================================================

  function cleanHindi(text) {
    if (!text) return '';
    return String(text).replace(/\ufffd/g, '').trim();
  }

  function resolveVoterFields(v) {
    const slNo = v.sl_no || v.serial_no || '-';
    const wardNo = v.ward_no;
    const partNo = v.part_no || 1;
    const isPanchayat = wardNo && String(wardNo).trim() !== '' && String(wardNo) !== '0';
    
    const name = cleanHindi(v.name_hi || v.name_en || 'मतदाता');
    const relName = cleanHindi(v.rel_name_hi || v.rel_name_en || '-');
    const relType = cleanHindi(v.rel_type) || 'पिता/पति';
    const age = v.age || '-';
    const houseNo = v.house_no || '-';
    const epicNo = cleanHindi(v.epic_no) || '-';

    return { slNo, wardNo, partNo, isPanchayat, name, relName, relType, age, houseNo, epicNo };
  }

  /**
   * Render strictly official voter slip on offscreen Canvas matching Photo 2
   */
  function renderSlipToCanvas(voters, boothName) {
    if (!Array.isArray(voters)) voters = [voters];
    if (voters.length === 0) return null;

    const canvas = document.createElement('canvas');
    canvas.width = THERMAL_WIDTH; // 384px

    // Dynamic height calculation
    const lineHeight = 30;
    const dividerHeight = 16;
    const voterBlockHeight = (lineHeight * 5) + dividerHeight; // ~166px per elector
    const boothBlockHeight = lineHeight * 3; // ~90px
    const totalHeight = 24 + (voters.length * voterBlockHeight) + boothBlockHeight + 30;

    canvas.height = totalHeight;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });

    // Background: Crisp Pure White
    ctx.fillStyle = '#FFFFFF';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Text Settings
    ctx.fillStyle = '#000000';
    ctx.textBaseline = 'top';

    const marginX = 10;
    const contentWidth = THERMAL_WIDTH - (marginX * 2);
    let curY = 10;

    // Helper: Draw Dashed Divider
    function drawDashedLine(y) {
      ctx.beginPath();
      ctx.strokeStyle = '#000000';
      ctx.lineWidth = 1.5;
      ctx.setLineDash([6, 4]);
      ctx.moveTo(marginX, y);
      ctx.lineTo(marginX + contentWidth, y);
      ctx.stroke();
      ctx.setLineDash([]); // reset
    }

    // Top Dashed Line
    drawDashedLine(curY);
    curY += 10;

    voters.forEach((v, idx) => {
      const f = resolveVoterFields(v);

      if (idx > 0) {
        drawDashedLine(curY);
        curY += 10;
      }

      // Line 1: क्रम सं : X      भाग सं : Y (या वार्ड सं : Z)
      ctx.font = 'bold 21px "Noto Sans Devanagari", "Mangal", -apple-system, system-ui, sans-serif';
      ctx.textAlign = 'left';
      ctx.fillText(`क्रम सं : ${f.slNo}`, marginX, curY);

      ctx.textAlign = 'right';
      const partOrWardText = f.isPanchayat 
        ? `वार्ड सं : ${f.wardNo}` 
        : `भाग सं : ${f.partNo}`;
      ctx.fillText(partOrWardText, marginX + contentWidth, curY);
      curY += lineHeight;

      // Line 2: नाम : <Name>
      ctx.textAlign = 'left';
      ctx.fillText(`नाम : ${f.name}`, marginX, curY);
      curY += lineHeight;

      // Line 3: पिता/पति : <Relative Name>
      ctx.fillText(`${f.relType} : ${f.relName}`, marginX, curY);
      curY += lineHeight;

      // Line 4: उम्र : X      मकान नंबर : Y
      ctx.fillText(`उम्र : ${f.age}`, marginX, curY);
      ctx.textAlign = 'right';
      ctx.fillText(`मकान नंबर : ${f.houseNo}`, marginX + contentWidth, curY);
      curY += lineHeight;

      // Line 5: वोटर ID : <EPIC>
      ctx.textAlign = 'left';
      ctx.fillText(`वोटर ID : ${f.epicNo}`, marginX, curY);
      curY += lineHeight + 4;
    });

    // Booth Block
    drawDashedLine(curY);
    curY += 10;

    ctx.font = 'bold 19px "Noto Sans Devanagari", "Mangal", -apple-system, system-ui, sans-serif';
    ctx.textAlign = 'left';
    
    // Multi-line booth name wrapping
    const boothPrefix = 'बूथ पता : ';
    const cleanBooth = cleanHindi(boothName) || 'राजकीय मतदान केंद्र';
    const fullBoothText = boothPrefix + cleanBooth;

    const words = fullBoothText.split(/\s+/);
    let line = '';
    const maxTextWidth = contentWidth;

    for (let n = 0; n < words.length; n++) {
      const testLine = line + (line ? ' ' : '') + words[n];
      const metrics = ctx.measureText(testLine);
      if (metrics.width > maxTextWidth && n > 0) {
        ctx.fillText(line, marginX, curY);
        line = words[n];
        curY += 26;
      } else {
        line = testLine;
      }
    }
    if (line) {
      ctx.fillText(line, marginX, curY);
      curY += 26;
    }

    // Bottom Dashed Line
    curY += 6;
    drawDashedLine(curY);
    curY += 16;

    // Crop or adjust canvas to exact content height
    const finalCanvas = document.createElement('canvas');
    finalCanvas.width = THERMAL_WIDTH;
    finalCanvas.height = Math.max(100, Math.ceil(curY));
    const finalCtx = finalCanvas.getContext('2d');
    finalCtx.fillStyle = '#FFFFFF';
    finalCtx.fillRect(0, 0, finalCanvas.width, finalCanvas.height);
    finalCtx.drawImage(canvas, 0, 0);

    return finalCanvas;
  }

  // =========================================================================
  // 3. ESC/POS MONOCHROME RASTERIZER (GS v 0)
  // =========================================================================

  function canvasToEscPos(canvas) {
    const ctx = canvas.getContext('2d');
    const width = canvas.width;   // 384
    const height = canvas.height;
    const imgData = ctx.getImageData(0, 0, width, height);
    const data = imgData.data;

    const widthBytes = Math.ceil(width / 8); // 48 bytes
    const buffer = [];

    // ESC @ (Initialize)
    buffer.push(0x1B, 0x40);

    // ESC 3 0 (Set line spacing to 0)
    buffer.push(0x1B, 0x33, 0x00);

    // GS v 0 0 xL xH yL yH (Standard ESC/POS Raster Bit Image)
    const xL = widthBytes & 0xFF;
    const xH = (widthBytes >> 8) & 0xFF;
    const yL = height & 0xFF;
    const yH = (height >> 8) & 0xFF;

    buffer.push(0x1D, 0x76, 0x30, 0x00, xL, xH, yL, yH);

    // 1-Bit Packed Bitmap
    for (let y = 0; y < height; y++) {
      for (let xByte = 0; xByte < widthBytes; xByte++) {
        let byteVal = 0;
        for (let bit = 0; bit < 8; bit++) {
          const x = xByte * 8 + bit;
          if (x < width) {
            const idx = (y * width + x) * 4;
            const r = data[idx];
            const g = data[idx + 1];
            const b = data[idx + 2];
            const a = data[idx + 3];

            // Luminance calculation
            const lum = (a < 128) ? 255 : (0.299 * r + 0.587 * g + 0.114 * b);
            if (lum < 185) { // Black pixel
              byteVal |= (1 << (7 - bit));
            }
          }
        }
        buffer.push(byteVal);
      }
    }

    // Paper Feed: ESC d 4 (Feed 4 lines for easy tear-off)
    buffer.push(0x1B, 0x64, 0x04);

    return new Uint8Array(buffer);
  }

  async function writeBytesInChunks(characteristic, uint8Data, chunkSize = 120) {
    for (let offset = 0; offset < uint8Data.length; offset += chunkSize) {
      const chunk = uint8Data.slice(offset, offset + chunkSize);
      if (characteristic.properties.writeWithoutResponse) {
        await characteristic.writeValueWithoutResponse(chunk);
      } else {
        await characteristic.writeValue(chunk);
      }
      // Small pause between BLE packets prevents thermal buffer overflow
      await new Promise(r => setTimeout(r, 20));
    }
  }

  // =========================================================================
  // 4. PRINT EXECUTION DISPATCHERS
  // =========================================================================

  /**
   * Primary entry point: Print via Bluetooth (HOP-HL58)
   */
  async function printThermalSlip(voters, boothName) {
    if (isPrinting) {
      if (window.showToast) window.showToast('एक पर्ची पहले से प्रिंट हो रही है...', 'warning', 2000);
      return;
    }

    if (!Array.isArray(voters)) voters = [voters];
    if (voters.length === 0) {
      if (window.showToast) window.showToast('कोई मतदाता चयनित नहीं है', 'error', 2500);
      return;
    }

    // 1. Resolve Bluetooth Characteristic
    const char = await getActiveCharacteristic();
    if (!char) {
      // If user cancelled Bluetooth or it failed, offer browser fallback
      return;
    }

    isPrinting = true;
    try {
      if (window.showToast) window.showToast('🖨️ पर्ची प्रिंट हो रही है...', 'info', 2000);

      // 2. Render Canvas
      const canvas = renderSlipToCanvas(voters, boothName);
      if (!canvas) throw new Error('कैनवास तैयार नहीं हो सका');

      // 3. Convert to ESC/POS Raster Bytes
      const escPosData = canvasToEscPos(canvas);

      // 4. Stream over Bluetooth
      await writeBytesInChunks(char, escPosData, 120);

      if (window.triggerHaptic) window.triggerHaptic('success');
      if (window.showToast) window.showToast('✓ पर्ची सफलतापूर्वक प्रिंट हो गई!', 'success', 3000);
    } catch (err) {
      console.error('Thermal print error:', err);
      if (window.showToast) window.showToast('प्रिंटिंग में त्रुटि: ' + (err.message || 'Error'), 'error', 4000);
    } finally {
      isPrinting = false;
    }
  }

  /**
   * Browser 58mm System Print Fallback (For Laptops / USB cable)
   */
  function printThermalViaBrowser(voters, boothName) {
    if (!Array.isArray(voters)) voters = [voters];
    if (voters.length === 0) return;

    const canvas = renderSlipToCanvas(voters, boothName);
    if (!canvas) return;

    const imgDataUrl = canvas.toDataURL('image/png');
    const win = window.open('', '_blank');
    if (!win) {
      alert('कृपया पॉपअप विंडो की अनुमति दें।');
      return;
    }

    win.document.write(`
      <!DOCTYPE html>
      <html>
      <head>
        <title>मतदाता पर्ची (58mm)</title>
        <style>
          @page { size: 58mm auto; margin: 0; }
          body { margin: 0; padding: 0; display: flex; justify-content: center; background: #fff; }
          img { width: 48mm; max-width: 100%; display: block; image-rendering: pixelated; }
        </style>
      </head>
      <body>
        <img src="${imgDataUrl}" onload="window.print();window.close();" />
      </body>
      </html>
    `);
    win.document.close();
  }

  // =========================================================================
  // 5. 1-TAP QUICK PRINT HELPER
  // =========================================================================

  window.quickThermalPrintVoter = async function (voterId) {
    let v = null;
    if (window.APP_STATE && window.APP_STATE.selectedVoters) {
      v = window.APP_STATE.selectedVoters.get(voterId);
    }
    if (!v && window.APP_STATE && window.APP_STATE.currentResults) {
      v = window.APP_STATE.currentResults.find(x => (x.id || x.voter_id) === voterId);
    }
    if (!v && window.currentResults) {
      v = window.currentResults.find(x => (x.id || x.voter_id) === voterId);
    }

    if (!v) {
      // Fetch voter data quickly from server
      try {
        const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';
        const res = await fetch(`/api/slip_text?id=${voterId}` + (curTenant ? `&tenant=${encodeURIComponent(curTenant)}` : ''));
        const data = await res.json();
        if (data && data.voter) {
          v = data.voter;
        } else if (data && data.voters && data.voters.length > 0) {
          v = data.voters[0];
        }
      } catch (e) {
        console.warn('Could not fetch voter for quick thermal print:', e);
      }
    }

    if (!v) {
      if (typeof window.openSlipModalForVoter === 'function') {
        window.openSlipModalForVoter(voterId);
      }
      return;
    }

    // Struck off check
    if (String(v.status || '').trim().toLowerCase() === 'deleted') {
      if (window.showToast) window.showToast('यह मतदाता सूची से विलोपित है — पर्ची जारी नहीं की जा सकती', 'error', 4000);
      return;
    }

    let boothName = v.booth_name;
    if (!boothName && typeof getBoothName === 'function') {
      boothName = getBoothName(v);
    }
    if (!boothName) {
      boothName = 'राजकीय मतदान केंद्र';
    }

    await printThermalSlip([v], boothName);
  };

  // Direct Slip Modal Print Dispatcher (Guaranteed to be defined even if slip_engine is delayed/cached)
  window.thermalPrintCurrentSlip = function () {
    let targets = [];
    if (typeof window.getCurrentSlipVoters === 'function') {
      targets = window.getCurrentSlipVoters();
    }
    if (!targets || targets.length === 0) {
      if (window.APP_STATE && window.APP_STATE.selectedVoters && window.APP_STATE.selectedVoters.size > 0) {
        targets = Array.from(window.APP_STATE.selectedVoters.values());
      }
    }
    if (!targets || targets.length === 0) {
      if (window.showToast) window.showToast('प्रिंट करने के लिए कोई मतदाता नहीं है', 'error');
      else alert('प्रिंट करने के लिए कोई मतदाता नहीं है');
      return;
    }

    let boothName = 'राजकीय मतदान केंद्र';
    if (typeof getBoothName === 'function') {
      boothName = getBoothName(targets[0]);
    } else if (targets[0].booth_name) {
      boothName = targets[0].booth_name;
    }

    printThermalSlip(targets, boothName);
  };

  // =========================================================================
  // 6. GLOBAL EXPORTS
  // =========================================================================

  window.ThermalPrinter = {
    connect: connectBluetoothPrinter,
    print: printThermalSlip,
    printCurrentSlip: window.thermalPrintCurrentSlip,
    printBrowserFallback: printThermalViaBrowser,
    renderCanvas: renderSlipToCanvas,
    quickPrint: window.quickThermalPrintVoter,
    isSupported: isBluetoothSupported
  };

  // Check saved printer name & auto-connect in PWA on startup
  document.addEventListener('DOMContentLoaded', () => {
    const savedName = localStorage.getItem('sb_thermal_printer_name');
    if (savedName) {
      updateStatusUI(false, savedName);
    }

    // Silent background PWA handshake if already paired previously
    if (navigator.bluetooth && navigator.bluetooth.getDevices) {
      navigator.bluetooth.getDevices().then(devices => {
        if (devices && devices.length > 0) {
          const dev = devices.find(d => d.name === savedName) || devices[0];
          if (dev && dev.gatt) {
            dev.addEventListener('gattserverdisconnected', onDeviceDisconnected);
            dev.gatt.connect().then(async server => {
              gattServer = server;
              writeCharacteristic = await resolveWriteCharacteristic(server);
              const devName = dev.name || 'HOP-HL58';
              updateStatusUI(true, devName);
              console.log('✓ SmartBooth PWA: Auto-connected to thermal printer:', devName);
            }).catch(() => {
              // Printer might be off, silent ignore
            });
          }
        }
      }).catch(() => {});
    }
  });

})();
