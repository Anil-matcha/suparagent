// Preloaded into the app's main process (electron -r) so os.homedir() answers a temporary folder. Setting HOME itself
// would also hide the macOS keychain from the app, which then cannot start. Only the app's own question "where is the
// home folder" changes; nothing else in the process.
const os = require("node:os");

const home = process.env.SUPARAGENT_E2E_HOME;
if (home) os.homedir = () => home;
