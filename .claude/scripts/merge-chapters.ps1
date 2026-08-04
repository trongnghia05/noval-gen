# Gộp các chương rời trong một thư mục truyện thành MỘT file input duy nhất.
#
#   .\.claude\scripts\merge-chapters.ps1 -SourceFolder "input\Ten Truyen"
#
# Cấu trúc thư mục nguồn mong đợi (dạng scrape từ web):
#   <SourceFolder>/Chapter 1 - .../content.txt
#   <SourceFolder>/Chapter 2 - .../content.txt
#   ...
#
# Đầu ra: <thư mục cha của SourceFolder>/<tên SourceFolder>.md
# gồm header input template (ngôn ngữ + loại input) rồi đến toàn bộ chương,
# mỗi chương mở đầu bằng một dòng "Chapter N" — đúng format mà orchestrator
# dùng để đếm số chương gốc và tính mật độ từ/chương cho chế độ REWRITE.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SourceFolder,

    [string]$Language  = 'English',
    [ValidateSet('IDEA', 'PREMISE', 'REWRITE')]
    [string]$InputType = 'REWRITE',

    # Ghi đè file .md đã tồn tại mà không cần hỏi
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $SourceFolder -PathType Container)) {
    throw "Khong tim thay thu muc nguon: $SourceFolder"
}

$srcDir     = (Resolve-Path -LiteralPath $SourceFolder).Path.TrimEnd('\')
$folderName = Split-Path -Leaf $srcDir
$outFile    = Join-Path (Split-Path -Parent $srcDir) ($folderName + '.md')

if ((Test-Path -LiteralPath $outFile) -and -not $Force) {
    Write-Warning "File da ton tai, se ghi de: $outFile"
}

# --- Thu thập chương ------------------------------------------------------
# Số chương lấy từ TÊN THƯ MỤC, không lấy từ chapter.json (trường "n" trong đó
# là chỉ số nội bộ của trang nguồn, không phải số thứ tự chương).
$chapters = Get-ChildItem -LiteralPath $srcDir -Directory | ForEach-Object {
    if ($_.Name -match '^Chapter\s+(\d+)\b') {
        $txt  = Join-Path $_.FullName 'content.txt'
        $html = Join-Path $_.FullName 'content.html'
        [PSCustomObject]@{
            Num  = [int]$Matches[1]   # ép [int] để sort 2 < 10 (không phải sort chuỗi)
            Dir  = $_.Name
            Txt  = $txt
            Html = $html
        }
    }
} | Sort-Object Num

if (-not $chapters) { throw "Khong tim thay thu muc chuong nao khop 'Chapter <so>' trong: $srcDir" }

$dupes = $chapters | Group-Object Num | Where-Object { $_.Count -gt 1 }
if ($dupes) { throw ("Trung so chuong: " + (($dupes | ForEach-Object { $_.Name }) -join ', ')) }

# --- Gộp ------------------------------------------------------------------
$header = @"
# Input Tiểu Thuyết

## Ngôn ngữ viết
$Language

## Loại input

$InputType

## Thể loại (tuỳ chọn)
<!-- Để trống nếu muốn AI tự chọn -->


## Độ dài mong muốn (tuỳ chọn)
<!-- Để trống = REWRITE lấy đúng số chương + mật độ từ/chương của truyện gốc bên dưới. -->


## Nội dung input

"@

# Rác điều hướng của trang nguồn, không phải nội dung truyện
$navPattern = '^\s*(Next\s+Chapter\s*>>|>>\s*Next\s+Chapter|<<\s*Previous\s+Chapter|Previous\s+Chapter\s*<<)\s*$'

$sb = New-Object System.Text.StringBuilder
[void]$sb.AppendLine($header)
[void]$sb.AppendLine($folderName)
[void]$sb.AppendLine()

$totalWords = 0
$fromHtml   = @()
$emptyCh    = @()

foreach ($ch in $chapters) {
    if (Test-Path -LiteralPath $ch.Txt) {
        $lines = Get-Content -LiteralPath $ch.Txt -Encoding UTF8
    }
    elseif (Test-Path -LiteralPath $ch.Html) {
        # Dự phòng: chương thiếu content.txt thì bóc thẻ từ content.html
        $fromHtml += $ch.Num
        $raw = Get-Content -LiteralPath $ch.Html -Raw -Encoding UTF8
        $raw = [regex]::Replace($raw, '(?is)<(script|style)\b.*?</\1>', '')
        $raw = [regex]::Replace($raw, '(?i)<br\s*/?>|</p>|</div>|</h[1-6]>', "`n")
        $raw = [regex]::Replace($raw, '(?s)<[^>]+>', '')
        $lines = [System.Net.WebUtility]::HtmlDecode($raw) -split "`r?`n"
    }
    else {
        throw "Chuong $($ch.Num): khong co content.txt lan content.html trong '$($ch.Dir)'"
    }

    $body = (($lines | Where-Object { $_ -notmatch $navPattern }) -join "`r`n").Trim()
    if (-not $body) { $emptyCh += $ch.Num }

    $totalWords += ($body -split '\s+' | Where-Object { $_ -ne '' }).Count

    [void]$sb.AppendLine("Chapter $($ch.Num)")
    [void]$sb.AppendLine()
    [void]$sb.AppendLine($body)
    [void]$sb.AppendLine()
}

# UTF-8 khong BOM — tranh ky tu la o dau file khi agent doc lai
[System.IO.File]::WriteAllText($outFile, $sb.ToString(), (New-Object System.Text.UTF8Encoding($false)))

# --- Báo cáo --------------------------------------------------------------
$nums = $chapters | ForEach-Object { $_.Num }
$gaps = 1..($nums[-1]) | Where-Object { $nums -notcontains $_ }
$wpc  = [math]::Round($totalWords / $chapters.Count)

"OUTPUT_FILE       : $outFile"
"STORY_TITLE       : $folderName"
"LANGUAGE          : $Language"
"INPUT_TYPE        : $InputType"
"TOTAL_CHAPTERS    : $($chapters.Count)  (Chapter $($nums[0])..$($nums[-1]))"
"TOTAL_WORDS       : $totalWords"
"WORDS_PER_CHAPTER : $wpc"
"MISSING_CHAPTERS  : $(if ($gaps)     { $gaps -join ', ' }     else { 'none' })"
"EMPTY_CHAPTERS    : $(if ($emptyCh)  { $emptyCh -join ', ' }  else { 'none' })"
"RECOVERED_FROM_HTML: $(if ($fromHtml) { $fromHtml -join ', ' } else { 'none' })"
