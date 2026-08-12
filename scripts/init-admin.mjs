import { randomBytes, scryptSync } from "node:crypto";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const projectRoot = process.cwd();
const privateRoot = join(projectRoot, ".private");
const authFile = join(privateRoot, "admin-auth.json");
const accessFile = join(projectRoot, "ADMIN_ACCESS.txt");

mkdirSync(privateRoot, { recursive: true });

if (existsSync(authFile) && existsSync(accessFile)) {
  process.stdout.write("Admin credentials already exist.\n");
  process.exit(0);
}

const username = `fg_admin_${randomBytes(5).toString("hex")}`;
const password = `FG-${randomBytes(12).toString("base64url")}`;
const passwordSalt = randomBytes(24).toString("hex");
const passwordHash = scryptSync(password, passwordSalt, 64).toString("hex");
const config = {
  username,
  passwordSalt,
  passwordHash,
  sessionSecret: randomBytes(48).toString("base64url"),
  analyticsSecret: randomBytes(48).toString("base64url"),
};

writeFileSync(authFile, `${JSON.stringify(config, null, 2)}\n`, { encoding: "utf8", mode: 0o600, flag: "wx" });
writeFileSync(
  accessFile,
  [
    "FRIES GLOBAL - ADMIN ACCESS",
    "",
    "Admin path: /admin",
    `Username: ${username}`,
    `Password: ${password}`,
    "",
    "Keep this file private. Do not send it to website visitors.",
    "The public hostname may change; append /admin to the latest public URL.",
    "",
  ].join("\r\n"),
  { encoding: "utf8", mode: 0o600, flag: "wx" },
);
process.stdout.write("Admin credentials initialized.\n");
