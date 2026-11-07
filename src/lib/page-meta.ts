import { useEffect } from "react";

type PageMeta = {
  title: string;
  description: string;
  twitterCard?: "summary" | "summary_large_image";
};

function setMeta(attr: "name" | "property", key: string, content: string) {
  let el = document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${key}"]`);
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute(attr, key);
    document.head.appendChild(el);
  }
  el.setAttribute("content", content);
}

/** Client-side replacement for the per-route `head()` metadata TanStack Start rendered on the server. */
export function usePageMeta({ title, description, twitterCard = "summary" }: PageMeta) {
  useEffect(() => {
    document.title = title;
    setMeta("name", "description", description);
    setMeta("property", "og:title", title);
    setMeta("property", "og:description", description);
    setMeta("property", "og:type", "website");
    setMeta("name", "twitter:card", twitterCard);
  }, [title, description, twitterCard]);
}
