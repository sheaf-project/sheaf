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
          Supported sources: SimplyPlural (export file), PluralKit (export file
          or live API via your <code>pk;token</code>), Tupperbox (export file),
          Sheaf (export file), Octocon and compatible forks (via PK export), Prism
          (export file), PluralSpace (export file).
        </p>
        <Link to="/import">
          <Button variant="outline">Import data</Button>
        </Link>
      </CardContent>
    </Card>
  );
}
