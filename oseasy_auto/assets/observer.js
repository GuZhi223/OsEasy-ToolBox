'use strict';
const config = __CONFIG__;
const normalize = p => p.replace(/\//g, '\\').toLowerCase();
const trusted = new Set(config.trustedModules.map(normalize));
const renderers = new Set(config.renderers.map(normalize));
const watchedHandles = new Set();
const installed = new Set();
const callbacks = [];
const listeners = [];
const replacements = [];
const lastSent = new Map();
const caps = {broadcast: false, keyboard: false, network: false, usb: false};
let disposed = false;

function report(kind, detail = {}) {
  const key = kind + ':' + (detail.api || detail.image || '') + (kind === 'broadcast_request' ? JSON.stringify(detail.addresses) : '');
  const now = Date.now();
  if (now - (lastSent.get(key) || 0) < 350) return;
  lastSent.set(key, now);
  send(Object.assign({kind: kind}, detail));
}
function broadcastAddresses(command) {
  const pattern = /(^|[{,\s])(["#]?)(teacher_ip|remote)\2\s*:\s*(["#]?)(\d{1,3}(?:\.\d{1,3}){3})\4(?=[,}\s]|$)/gi;
  const addresses = [];
  let match;
  while ((match = pattern.exec(command)) !== null && addresses.length < 8) {
    addresses.push({field: match[3].toLowerCase(), value: match[5]});
  }
  return addresses;
}
function text(p, wide) {
  if (p.isNull()) return '';
  try { return wide ? p.readUtf16String() : p.readAnsiString(); } catch (_) { return ''; }
}
function fileTrusted(path) {
  const expected = config.trustedHashes[normalize(path)];
  if (!expected) return false;
  try { return Checksum.compute('sha256', File.readAllBytes(path)) === expected; }
  catch (_) { return false; }
}
function exportAt(moduleName, name) {
  const module = Process.enumerateModules().find(m => m.name.toLowerCase() === moduleName.toLowerCase());
  return module ? module.findExportByName(name) : null;
}
function attach(address, tag, handlers) {
  if (!address || installed.has(address.toString())) return false;
  const listener = Interceptor.attach(address, handlers);
  installed.add(address.toString());
  listeners.push(listener);
  return true;
}
function replace(address, returnType, args, handler, abi) {
  if (!address || installed.has(address.toString())) return false;
  const original = new SystemFunction(address, returnType, args, abi);
  const callback = new NativeCallback(function (...values) {
    return handler.call(this, original, values);
  }, returnType, args, abi);
  Interceptor.replace(address, callback);
  installed.add(address.toString());
  callbacks.push(callback);
  replacements.push(address);
  return true;
}
function originalCall(original, values, context) {
  const result = original(...values);
  context.lastError = result.lastError;
  return result.value;
}

// Restrict the argument transformation to explicit paths of the pinned renderer.
function addProcessObserver(wide) {
  const api = wide ? 'CreateProcessW' : 'CreateProcessA';
  const address = exportAt('kernel32.dll', api);
  const ok = attach(address, api, {
    onEnter(args) {
      this.changed = false;
      this.renderer = false;
      const application = text(args[0], wide);
      const command = text(args[1], wide);
      const first = command.trim().match(/^(?:"([^"]+)"|([^\s]+))/);
      const executable = application || (first ? first[1] || first[2] : '');
      const normalized = normalize(executable.replace(/^"|"$/g, ''));
      const image = normalized.split('\\').pop() || '';
      if (!renderers.has(normalized) || !fileTrusted(executable.replace(/^"|"$/g, ''))) {
        if (image) report('program_request', {image: image, api: api});
        return;
      }
      this.renderer = true;
      this.addresses = broadcastAddresses(command);
      if (!command) return;
      const adjusted = command.replace(/(^|[{,\s])(["#]?fullscreen["#]?\s*:\s*)(1|true)(?=[,}\s]|$)/gi,
                                       (_, lead, key) => lead + key + '0');
      if (adjusted !== command) {
        this.commandStorage = wide ? Memory.allocUtf16String(adjusted) : Memory.allocAnsiString(adjusted);
        args[1] = this.commandStorage;
        this.changed = true;
      }
    },
    onLeave(result) {
      if (this.renderer) report('broadcast_request', {changed: this.changed, launched: result.toInt32() !== 0,
                                                    addresses: this.addresses || []});
    }
  });
  caps.broadcast = caps.broadcast || ok;
}

function addKeyboardGuard() {
  for (const wide of [true, false]) {
    attach(exportAt('kernel32.dll', wide ? 'CreateFileW' : 'CreateFileA'), 'driver-open', {
      onEnter(args) {
        this.isKeyboard = normalize(text(args[0], wide)) === '\\\\.\\oekbdfilter';
      },
      onLeave(result) {
        if (this.isKeyboard && !result.equals(ptr(-1))) watchedHandles.add(result.toString());
      }
    });
  }
  attach(exportAt('kernel32.dll', 'CloseHandle'), 'handle-close', {
    onEnter(args) { watchedHandles.delete(args[0].toString()); }
  });
  const abi = Process.arch === 'ia32' ? 'stdcall' : 'win64';
  caps.keyboard = replace(exportAt('kernel32.dll', 'DeviceIoControl'), 'int',
    ['pointer', 'uint', 'pointer', 'uint', 'pointer', 'uint', 'pointer', 'pointer'],
    function (original, a) {
      if (watchedHandles.has(a[0].toString()) && a[1] === 0xB2020 && a[3] === 16 && !a[2].isNull() && a[7].isNull()) {
        try {
          const type = a[2].readU32();
          const count = a[2].add(4).readU32();
          if (type === 2 && count > 0 && count <= 8) {
            if (!a[6].isNull()) a[6].writeU32(0);
            this.lastError = 5;
            report('keyboard_denied', {api: 'OeKbdFilter', count: count});
            return 0;
          }
        } catch (_) { /* Unknown buffers pass through; never broaden the match. */ }
      }
      return originalCall(original, a, this);
    }, abi);
}

function addModuleGuards(module) {
  if (!trusted.has(normalize(module.path)) || !fileTrusted(module.path)) return;
  const name = module.name.toLowerCase();
  const abi = Process.arch === 'ia32' ? 'mscdecl' : 'win64';
  if (name === 'oenetlimit.dll') {
    // Static evidence establishes one pointer argument and an AL boolean return.
    const added = replace(module.findExportByName('DisableInternet'), 'bool', ['pointer'], function () {
      this.lastError = 5;
      report('network_denied', {api: 'DisableInternet'});
      return 0;
    }, abi);
    caps.network = caps.network || added;
  }
  if (name === 'easyusbctrl.dll') {
    // StartWorking writes persistent state. Refuse new calls without calling it.
    const added = replace(module.findExportByName('EasyUsb_StartWorking'), 'int', [], function () {
      this.lastError = 5;
      report('usb_denied', {api: 'EasyUsb_StartWorking'});
      return 5;
    }, abi);
    caps.usb = caps.usb || added;
  }
  report('capabilities', {capabilities: caps});
}

addProcessObserver(true);
addProcessObserver(false);
if (config.role !== 'renderer') addKeyboardGuard();
const moduleObserver = Process.attachModuleObserver({
  onAdded(module) {
    try { addModuleGuards(module); }
    catch (error) { report('hook_error', {api: module.name, error: String(error)}); }
  }
});
Interceptor.flush();
send({kind: 'ready', architecture: Process.arch, capabilities: caps});

rpc.exports = {
  status() { return {architecture: Process.arch, capabilities: caps}; },
  dispose() {
    if (disposed) return;
    disposed = true;
    moduleObserver.detach();
    for (const listener of listeners) listener.detach();
    for (const address of replacements) Interceptor.revert(address);
    Interceptor.flush();
  }
};
