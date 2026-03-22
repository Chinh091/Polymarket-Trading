import { NextRequest, NextResponse } from "next/server";

export function middleware(req: NextRequest) {
  const password = process.env.DASHBOARD_PASSWORD;

  // If no password is set, allow access (dev mode)
  if (!password) return NextResponse.next();

  const auth = req.headers.get("authorization") ?? "";
  const [scheme, encoded] = auth.split(" ");

  if (scheme === "Basic" && encoded) {
    const decoded = Buffer.from(encoded, "base64").toString("utf-8");
    // Accept any username, check only the password
    const userPassword = decoded.split(":").slice(1).join(":");
    if (userPassword === password) {
      return NextResponse.next();
    }
  }

  return new NextResponse("Unauthorized", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="Trading Dashboard"',
    },
  });
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
