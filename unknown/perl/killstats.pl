#!/usr/bin/perl

use CGI ':standard';

# CONSTANTS

$KILL_FILE = "eruanno.txt";
$CHAR_NAME = "Eruanno";
$VICTIM_URL = "http://www.tibia.com/community/?subtopic=characters&name=%s";
@ROW_BG_COLOR = ("D4C0A1", "F1E0C6");

# HTML HEADERS

print << "EOF";
Content-type: text/html

<html>
<head><title>Kill Stats for $CHAR_NAME</title>
<link href="http://static.tibia.com/styles/basic.css" rel="stylesheet" type="text/css">
</head>
<body>
<div id="Bodycontainer">

<p>&nbsp;</p>

<div id="ContentColumn">
<div class="Content">
<div class="BoxContent" style="background-image:url(http://static.tibia.com/images/global/content/scroll.gif);">

<p><a href="#Top"></a>Perl/CGI dynamic kill stats page.<br />
<strong>Still in development, you'll be able to submit your kills below.</strong><br />
Please add your kills to the box below by pasting in the Player Kills table on your character.</p>

<form action="newkills.pl" method="POST">
<p><textarea name="newkills" rows="1" cols="80"></textarea></p>
<p><input type="submit" value="Add my kills"><p></form>

<h1>Kill Stats for Eruanno</h1>

EOF

# PRINT KILLS

if (open (KILLFILE, "$KILL_FILE")) {
	my (@filedata) = <KILLFILE>;
        &print_table ("Character Information\t2", "Name:\t$CHAR_NAME", "Recorded Kills:\t" . @filedata);
        print "<br /><br />\n";
	&process_kills(@filedata);
} else {
	&print_table ("header\t2", "name:\tmatt", "character:\teruanno");
}

print << "EOF";

</div></div></div>
<div class="Border_1" style="background-image:url(http://static.tibia.com/images/global/content/border-1.gif);"></div>
	<div class="CornerWrapper-b"><div class="Corner-bl" style="background-image:url(http://static.tibia.com/images/global/content/corner-bl.gif);"></div></div>
		<div class="CornerWrapper-b"><div class="Corner-br" style="background-image:url(http://static.tibia.com/images/global/content/corner-br.gif);"></div></div>
  		</div>
        </div>
</div>

<div id="Footer">
Some Rights<br/>
<a href="http://www.powershout.com/">Powershout streaming</a> | 
<a href="http://www.tibia.com/community/?subtopic=whoisonline&world=Dolera&order=level">Dolera Online list by Level</a> | 
<a href="http://erig.net/xphist.php?world=Dolera">XP Histories for Dolera</a>
</div>

</div></body></html>

EOF

sub make_link_tag {
# takes format string then arguments
	my $link = $_[1];
        $link =~ tr/ /+/;
 	return "<a href=\"" . sprintf($_[0], $link) . "\">$_[1]</a>";
}

sub process_kills {
# receives an array of lines from the kill list file
        my (@filedata) = @_;
        my (@table_raw) = ("Unfiltered kills, by date\t3\t\t\t<b class=green>|</b>");
        my (%kills_victim, %kills_level, @level_victim, $record_count);
	foreach (@filedata) {
        	if (my ($date, $victim, $level) = ($_ =~ /^((?:[A-Z][a-z]{2} \d{2} \d{4}, \d{2}:\d{2}:\d{2}|\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}) [A-Z]{3,4})\tKilled (.*) at Level (\d+)\t\w+$/)) {
                	my $victimLink = &make_link_tag($VICTIM_URL, $victim);
			push @table_raw, $date . "\tKilled $victimLink at Level $level\tok";
                        $kills_victim{$victim}++;
                        $kills_level{$level}++;
                        push @level_victim, "$level\t" . &make_link_tag($VICTIM_URL, $victim);
                } else {
                	push @table_raw, "error!!!! farrrk";
                }
        }
	# LEVEL BY KILLS
	my (@table_level_by_kills) = ("Kills by level\t2<b><u>|</b></u>\t\t", "Victim Level\tKill Count");
        $record_count = 0;
	foreach (sort {$kills_level{$b} <=> $kills_level{$a} or $b <=> $a} keys %kills_level) {
        	push @table_level_by_kills, "$_\t$kills_level{$_}";
                $record_count++;
                if ($record_count < 0) {last;}
        }
        &print_table (@table_level_by_kills);
        print "<br /><br />\n";
        # VICTIM BY KILLS
        my (@table_victim_by_kills) = ("Victim by kills\t2<b><u>|</b></u>", "Victim Name\tNumber of kills");
        $record_count = 0;
        foreach (sort {$kills_victim{$b} <=> $kills_victim{$a}} keys %kills_victim) {
        	push @table_victim_by_kills, &make_link_tag($VICTIM_URL, $_) . "\t$kills_victim{$_}";
                $record_count++;
                if ($record_count > 30) {last;}
        }
        &print_table (@table_victim_by_kills);
        print "<br /><br />\n";
        # VICTIM BY LEVEL
        my (@table_victims_by_level) = ("Victims by level\t2<b><u>|</b></u>", "Victim level at death\tName");
        $record_count = 0;
        foreach (sort {($b =~ /^(\d+)\t/)[0] <=> ($a =~ /^(\d+)\t/)[0] or $a cmp $b} @level_victim) {
        	push @table_victims_by_level, "$_";
                $record_count++;
                if ($record_count > 40) {last;}
        }
        &print_table (@table_victims_by_level);
        print "<br /><br />\n";
        # RAW
        &print_table (@table_raw);
}

sub print_table {
# takes an array of rows columns split by \t
	print "<TABLE BORDER=0 CELLSPACING=1 CELLPADDING=4 WIDTH=100%>\n";
        my @header = split (/\t/, $_[0]);
	print "<a name=\"#$header[0]\"></a>"
        . "<TR BGCOLOR=#505050><td colspan=" . ($header[1] =~ /^(\d+)/)[0] . " CLASS=white width=100%><B>$header[0]</B></TD></TR>\n";
	for ($r = 1; $_[$r]; $r++) {
        	print "<tr bgcolor=#", $ROW_BG_COLOR[$r%2], ">";
        	my (@cells) = split (/\t/, $_[$r]);
                for ($c = 0; $cells[$c]; $c++) {
                        unless ($header[$c+2]) {$header[$c+2] = "";}
                        my ($fore, $aft) = ("","");
                        if ($r == 1 and $header[1] =~ /\|/) {
                        	($fore, $aft) = ($header[1] =~ /^\d*([^\|\t]*)\|([^\|\t]*)/);
                        } else {
                        	($fore, $aft) = ($header[$c+2] =~ /^\d*([^\|\t]*)\|([^\|\t]*)/);
                        }
                	print "<TD>", $fore, $cells[$c], $aft, "</TD>";
                }
                print "</tr>\n";
        }
        print "</table>\n" .
        "<center><a href=\"#Top\"><u>Back to Top</u></a></center>\n";
}                       #TD COLSPAN=2