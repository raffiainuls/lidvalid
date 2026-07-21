import { Navigate } from "react-router-dom";
import { useMe } from "@/hooks/use-me";
import LandingPage from "@/pages/landing-page";

// `/` shows the public landing page when logged out; an already-authenticated
// visit redirects straight to /dashboard instead of showing the marketing page.
export default function HomeRoute() {
  const { data, isLoading } = useMe();
  if (!isLoading && data) {
    return <Navigate to="/dashboard" replace />;
  }
  return <LandingPage />;
}
