import { Link } from "react-router";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function DataImportCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Import data</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground mb-3">
          Supported formats: SimplyPlural, Sheaf
        </p>
        <Link to="/import">
          <Button variant="outline">Import data</Button>
        </Link>
      </CardContent>
    </Card>
  );
}
