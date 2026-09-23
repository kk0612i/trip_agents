import type { SearchCandidate } from "./types";

interface PlaceImage { url: string; credit: string; source: string }

/** 图片仅按已核对的城市和地点名匹配，避免用通用风景照冒充搜索结果。 */
const changshaImages: Record<string, PlaceImage> = {
  "湖南博物院": {
    url: "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/Entrance_of_Hunan_Museum%2C_2018-09-28.jpg/500px-Entrance_of_Hunan_Museum%2C_2018-09-28.jpg",
    credit: "Siyuwj / Wikimedia Commons / CC BY-SA 4.0",
    source: "https://commons.wikimedia.org/wiki/File:Entrance_of_Hunan_Museum,_2018-09-28.jpg",
  },
  "岳麓书院": {
    url: "https://upload.wikimedia.org/wikipedia/commons/thumb/9/97/Yuelu_academy.jpg/500px-Yuelu_academy.jpg",
    credit: "xiquinhosilva / Wikimedia Commons / CC BY 2.0",
    source: "https://commons.wikimedia.org/wiki/File:Yuelu_academy.jpg",
  },
  "橘子洲": {
    url: "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e0/Orange_Isle_2021122636.jpg/500px-Orange_Isle_2021122636.jpg",
    credit: "Huangdan2060 / Wikimedia Commons / CC BY 3.0",
    source: "https://commons.wikimedia.org/wiki/File:Orange_Isle_2021122636.jpg",
  },
  "太平老街": {
    url: "https://upload.wikimedia.org/wikipedia/commons/thumb/3/30/Taiping_Street%2C_Changsha.jpg/500px-Taiping_Street%2C_Changsha.jpg",
    credit: "EditQ / Wikimedia Commons / CC BY-SA 4.0",
    source: "https://commons.wikimedia.org/wiki/File:Taiping_Street,_Changsha.jpg",
  },
};

/** 返回已知地点实景图及署名；无匹配时交由界面显示地点图标。 */
export function getPlaceImage(candidate: SearchCandidate): PlaceImage | null {
  if (!candidate.city.includes("长沙")) return null;
  const name = candidate.name === "湖南省博物馆" ? "湖南博物院" : candidate.name;
  return changshaImages[name] ?? null;
}
