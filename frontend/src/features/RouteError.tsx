import { Compass } from "lucide-react";
import { Link, useRouteError } from "react-router";
import { ErrorState, EmptyState } from "@/components/feedback";
import { Button } from "@/components/ui/button";

export function RouteError({ notFound }: { notFound?: boolean }) {
  const error = useRouteError();
  if (notFound || !error) {
    return (
      <div className="p-6">
        <EmptyState
          icon={<Compass />}
          title="Page not found"
          description="The page you're looking for doesn't exist or was moved."
          action={
            <Button asChild variant="outline" size="sm">
              <Link to="/">Back to dashboard</Link>
            </Button>
          }
        />
      </div>
    );
  }
  return (
    <div className="p-6">
      <ErrorState error={error} title="Something went wrong" onRetry={() => window.location.reload()} />
    </div>
  );
}
