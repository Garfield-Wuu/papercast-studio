/**
 * Mirror of MiniMax's public system-voice catalogue, narrowed to the
 * languages this lab actually presents in (中文 + English). Source:
 * https://platform.minimaxi.com/docs/llms.txt → 系统音色页（用户在 P8
 * 验收里粘贴的清单）。
 *
 * Why mirror rather than fetch: MiniMax doesn't expose this list as a
 * REST endpoint, and the catalogue changes maybe twice a year. Coding
 * it as static data lets the wizard render synchronously and keeps the
 * voice browser usable even when the user hasn't configured a MiniMax
 * key (they still see the list, just can't preview).
 *
 * To add new voices: append to SYSTEM_VOICES and set the language tag.
 * Don't reorder — the array index is incidentally how the picker
 * renders, and stable ordering helps users build muscle memory.
 */
export interface SystemVoice {
  voice_id: string;
  /** Human label as published by MiniMax docs (中文 / English). */
  label: string;
  language: "zh-CN" | "en";
  /** Coarse category — the wizard uses it to group rows in long lists. */
  category: "neutral" | "young" | "mature" | "child" | "character" | "broadcaster";
}

// REGION: zh-CN — 中文（普通话）, P8_VOICES_PLACEHOLDER
export const SYSTEM_VOICES: SystemVoice[] = [
  // ---- 中文（普通话）— 经典青年系列 ----
  { voice_id: "male-qn-qingse", label: "青涩青年", language: "zh-CN", category: "young" },
  { voice_id: "male-qn-jingying", label: "精英青年", language: "zh-CN", category: "young" },
  { voice_id: "male-qn-badao", label: "霸道青年", language: "zh-CN", category: "young" },
  { voice_id: "male-qn-daxuesheng", label: "青年大学生", language: "zh-CN", category: "young" },
  { voice_id: "female-shaonv", label: "少女", language: "zh-CN", category: "young" },
  { voice_id: "female-yujie", label: "御姐", language: "zh-CN", category: "mature" },
  { voice_id: "female-chengshu", label: "成熟女性", language: "zh-CN", category: "mature" },
  { voice_id: "female-tianmei", label: "甜美女性", language: "zh-CN", category: "young" },
  // ---- 中文（普通话）— jingpin 增强版 ----
  { voice_id: "male-qn-qingse-jingpin", label: "青涩青年 · beta", language: "zh-CN", category: "young" },
  { voice_id: "male-qn-jingying-jingpin", label: "精英青年 · beta", language: "zh-CN", category: "young" },
  { voice_id: "male-qn-badao-jingpin", label: "霸道青年 · beta", language: "zh-CN", category: "young" },
  { voice_id: "male-qn-daxuesheng-jingpin", label: "青年大学生 · beta", language: "zh-CN", category: "young" },
  { voice_id: "female-shaonv-jingpin", label: "少女 · beta", language: "zh-CN", category: "young" },
  { voice_id: "female-yujie-jingpin", label: "御姐 · beta", language: "zh-CN", category: "mature" },
  { voice_id: "female-chengshu-jingpin", label: "成熟女性 · beta", language: "zh-CN", category: "mature" },
  { voice_id: "female-tianmei-jingpin", label: "甜美女性 · beta", language: "zh-CN", category: "young" },
  // ---- 中文（普通话）— 童声 ----
  { voice_id: "clever_boy", label: "聪明男童", language: "zh-CN", category: "child" },
  { voice_id: "cute_boy", label: "可爱男童", language: "zh-CN", category: "child" },
  { voice_id: "lovely_girl", label: "萌萌女童", language: "zh-CN", category: "child" },
  { voice_id: "cartoon_pig", label: "卡通猪小琪", language: "zh-CN", category: "character" },
  // ---- 中文（普通话）— 角色化 ----
  { voice_id: "bingjiao_didi", label: "病娇弟弟", language: "zh-CN", category: "character" },
  { voice_id: "junlang_nanyou", label: "俊朗男友", language: "zh-CN", category: "character" },
  { voice_id: "chunzhen_xuedi", label: "纯真学弟", language: "zh-CN", category: "character" },
  { voice_id: "lengdan_xiongzhang", label: "冷淡学长", language: "zh-CN", category: "character" },
  { voice_id: "badao_shaoye", label: "霸道少爷", language: "zh-CN", category: "character" },
  { voice_id: "tianxin_xiaoling", label: "甜心小玲", language: "zh-CN", category: "character" },
  { voice_id: "qiaopi_mengmei", label: "俏皮萌妹", language: "zh-CN", category: "character" },
  { voice_id: "wumei_yujie", label: "妩媚御姐", language: "zh-CN", category: "character" },
  { voice_id: "diadia_xuemei", label: "嗲嗲学妹", language: "zh-CN", category: "character" },
  { voice_id: "danya_xuejie", label: "淡雅学姐", language: "zh-CN", category: "character" },
  // P8_VOICES_NEXT
  // ---- 中文（普通话）— 现代命名（沉稳/新闻/温润等）----
  { voice_id: "Chinese (Mandarin)_Reliable_Executive", label: "沉稳高管", language: "zh-CN", category: "broadcaster" },
  { voice_id: "Chinese (Mandarin)_News_Anchor", label: "新闻女声", language: "zh-CN", category: "broadcaster" },
  { voice_id: "Chinese (Mandarin)_Mature_Woman", label: "傲娇御姐", language: "zh-CN", category: "mature" },
  { voice_id: "Chinese (Mandarin)_Unrestrained_Young_Man", label: "不羁青年", language: "zh-CN", category: "young" },
  { voice_id: "Arrogant_Miss", label: "嚣张小姐", language: "zh-CN", category: "character" },
  { voice_id: "Robot_Armor", label: "机械战甲", language: "zh-CN", category: "character" },
  { voice_id: "Chinese (Mandarin)_Kind-hearted_Antie", label: "热心大婶", language: "zh-CN", category: "mature" },
  { voice_id: "Chinese (Mandarin)_HK_Flight_Attendant", label: "港普空姐", language: "zh-CN", category: "young" },
  { voice_id: "Chinese (Mandarin)_Humorous_Elder", label: "搞笑大爷", language: "zh-CN", category: "mature" },
  { voice_id: "Chinese (Mandarin)_Gentleman", label: "温润男声", language: "zh-CN", category: "neutral" },
  { voice_id: "Chinese (Mandarin)_Warm_Bestie", label: "温暖闺蜜", language: "zh-CN", category: "young" },
  { voice_id: "Chinese (Mandarin)_Male_Announcer", label: "播报男声", language: "zh-CN", category: "broadcaster" },
  { voice_id: "Chinese (Mandarin)_Sweet_Lady", label: "甜美女声", language: "zh-CN", category: "young" },
  { voice_id: "Chinese (Mandarin)_Southern_Young_Man", label: "南方小哥", language: "zh-CN", category: "young" },
  { voice_id: "Chinese (Mandarin)_Wise_Women", label: "阅历姐姐", language: "zh-CN", category: "mature" },
  { voice_id: "Chinese (Mandarin)_Gentle_Youth", label: "温润青年", language: "zh-CN", category: "young" },
  { voice_id: "Chinese (Mandarin)_Warm_Girl", label: "温暖少女", language: "zh-CN", category: "young" },
  { voice_id: "Chinese (Mandarin)_Kind-hearted_Elder", label: "花甲奶奶", language: "zh-CN", category: "mature" },
  { voice_id: "Chinese (Mandarin)_Cute_Spirit", label: "憨憨萌兽", language: "zh-CN", category: "character" },
  { voice_id: "Chinese (Mandarin)_Radio_Host", label: "电台男主播", language: "zh-CN", category: "broadcaster" },
  { voice_id: "Chinese (Mandarin)_Lyrical_Voice", label: "抒情男声", language: "zh-CN", category: "neutral" },
  { voice_id: "Chinese (Mandarin)_Straightforward_Boy", label: "率真弟弟", language: "zh-CN", category: "young" },
  { voice_id: "Chinese (Mandarin)_Sincere_Adult", label: "真诚青年", language: "zh-CN", category: "young" },
  { voice_id: "Chinese (Mandarin)_Gentle_Senior", label: "温柔学姐", language: "zh-CN", category: "young" },
  { voice_id: "Chinese (Mandarin)_Stubborn_Friend", label: "嘴硬竹马", language: "zh-CN", category: "character" },
  { voice_id: "Chinese (Mandarin)_Crisp_Girl", label: "清脆少女", language: "zh-CN", category: "young" },
  { voice_id: "Chinese (Mandarin)_Pure-hearted_Boy", label: "清澈邻家弟弟", language: "zh-CN", category: "young" },
  { voice_id: "Chinese (Mandarin)_Soft_Girl", label: "柔和少女", language: "zh-CN", category: "young" },
  // ---- 英文 ----
  { voice_id: "Santa_Claus", label: "Santa Claus", language: "en", category: "character" },
  { voice_id: "Grinch", label: "Grinch", language: "en", category: "character" },
  { voice_id: "Rudolph", label: "Rudolph", language: "en", category: "character" },
  { voice_id: "Arnold", label: "Arnold", language: "en", category: "character" },
  { voice_id: "Charming_Santa", label: "Charming Santa", language: "en", category: "character" },
  { voice_id: "Charming_Lady", label: "Charming Lady", language: "en", category: "mature" },
  { voice_id: "Sweet_Girl", label: "Sweet Girl", language: "en", category: "young" },
  { voice_id: "Cute_Elf", label: "Cute Elf", language: "en", category: "character" },
  { voice_id: "Attractive_Girl", label: "Attractive Girl", language: "en", category: "young" },
  { voice_id: "Serene_Woman", label: "Serene Woman", language: "en", category: "mature" },
  { voice_id: "English_Trustworthy_Man", label: "Trustworthy Man", language: "en", category: "neutral" },
  { voice_id: "English_Graceful_Lady", label: "Graceful Lady", language: "en", category: "mature" },
  { voice_id: "English_Aussie_Bloke", label: "Aussie Bloke", language: "en", category: "neutral" },
  { voice_id: "English_Whispering_girl", label: "Whispering Girl", language: "en", category: "young" },
  { voice_id: "English_Diligent_Man", label: "Diligent Man", language: "en", category: "neutral" },
  { voice_id: "English_Gentle-voiced_man", label: "Gentle-voiced Man", language: "en", category: "neutral" },
];

