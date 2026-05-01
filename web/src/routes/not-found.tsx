import { Link, useLocation } from "react-router";
import { Compass, Home } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

export function NotFoundPage() {
  const location = useLocation();

  return (
    <div className="flex min-h-[60vh] items-center justify-center px-4">
      <Card className="w-full max-w-md">
        <CardContent className="flex flex-col items-center gap-4 p-8 text-center">
          <Compass className="h-10 w-10 text-muted-foreground" />
          <div className="space-y-1">
            <h1 className="text-2xl font-semibold">Page not found</h1>
            <p className="text-sm text-muted-foreground">
              <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                {location.pathname}
              </code>{" "}
              isn't a route we recognise. It may have moved, been deleted, or
              never existed.
            </p>
          </div>
          <Button asChild>
            <Link to="/">
              <Home className="mr-2 h-4 w-4" /> Back to dashboard
            </Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
