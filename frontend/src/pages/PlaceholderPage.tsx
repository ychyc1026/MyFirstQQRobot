import { PageFrame } from "../components/PageLayout";
import { PageTitle } from "../components/PageTitle";
import { SurfaceCard } from "../components/SurfaceCard";

type PlaceholderPageProps = {
  title: string;
  description: string;
};

export function PlaceholderPage({ title, description }: PlaceholderPageProps) {
  return (
    <PageFrame>
      <PageTitle title={title} description={description} />
      <SurfaceCard fill>
        <p className="text-sm leading-6 text-muted-foreground">
          这一页会沿用同一套图标轨、白卡和胶囊。稍后接入已有 API，不接真实 QQ 或模型。
        </p>
      </SurfaceCard>
    </PageFrame>
  );
}
