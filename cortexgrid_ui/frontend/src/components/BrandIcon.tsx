import { siGrafana, siMlflow, siRay } from "simple-icons";
import { ExternalLink } from "lucide-react";

type SimpleIcon = { path: string };

const BRANDS: Record<string, SimpleIcon> = {
    grafana: siGrafana,
    mlflow: siMlflow,
    ray: siRay,
};

type Props = {
    slug: string;
    size?: number;
    className?: string;
};

export function BrandIcon({ slug, size = 18, className }: Props) {
    const icon = BRANDS[slug.toLowerCase()];
    if (!icon) {
        return <ExternalLink size={size} className={className} />;
    }
    return (
        <svg
            role="img"
            width={size}
            height={size}
            viewBox="0 0 24 24"
            className={className}
            fill="currentColor"
            aria-hidden="true"
        >
            <path d={icon.path} />
        </svg>
    );
}
